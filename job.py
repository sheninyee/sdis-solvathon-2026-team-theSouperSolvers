"""job.py — Pipeline runner for Federal Funds Matchmaker (MODE=job).

Run once and exit. Steps:
  1. Load profile from blob or local file
  2. Ingest raw opportunities from Grants.gov
  3. Normalize
  4. Score
  5. Write scored results to blob (scored/{account_id}/latest.json)
  6. Write run log to blob (runs/{timestamp}.json)

Environment variables:
  STORAGE_CONN         — Azure Storage connection string (optional; falls back to local)
  AOAI_ENDPOINT        — Azure OpenAI endpoint (optional; enables semantic scoring)
  AOAI_KEY             — Azure OpenAI key
  AOAI_EMBEDDING_MODEL — Embedding deployment name (default: text-embedding-3-small)
  PROFILE_ID           — Account/profile ID to process (default: ncdot)
  PROFILE_PATH         — Override local profile path (default: profiles/{PROFILE_ID}.json)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("job")

# Local module imports
from ingest import ingest_for_profile
from normalize import normalize_all
from score import load_profile, score_all

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STORAGE_CONN = os.environ.get("STORAGE_CONN", "")
CONTAINER = "ffm-data"
PROFILE_ID = os.environ.get("PROFILE_ID", "ncdot")
PROFILE_PATH = os.environ.get("PROFILE_PATH", f"profiles/{PROFILE_ID}.json")
AOAI_ENDPOINT = os.environ.get("AOAI_ENDPOINT", "")
AOAI_KEY = os.environ.get("AOAI_KEY", "")


# ---------------------------------------------------------------------------
# Blob helpers (duplicated from main.py to keep job.py standalone)
# ---------------------------------------------------------------------------
def _blob_client(blob_path: str):
    if not STORAGE_CONN:
        return None
    try:
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(STORAGE_CONN)
        return svc.get_blob_client(CONTAINER, blob_path)
    except Exception as e:
        logger.warning("Blob client error: %s", e)
        return None


def _read_blob(blob_path: str) -> Optional[dict]:
    bc = _blob_client(blob_path)
    if bc:
        try:
            return json.loads(bc.download_blob().readall())
        except Exception:
            pass
    return None


def _write_blob(blob_path: str, obj: dict) -> bool:
    bc = _blob_client(blob_path)
    if bc:
        try:
            bc.upload_blob(json.dumps(obj, indent=2), overwrite=True)
            logger.info("Wrote blob: %s", blob_path)
            return True
        except Exception as e:
            logger.warning("Blob write failed: %s", e)
    return False


def _write_local(rel_path: str, obj: dict):
    p = Path("data/sample") / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2))
    logger.info("Wrote local file: %s", p)


# ---------------------------------------------------------------------------
# Profile embedding (optional — requires Azure OpenAI)
# ---------------------------------------------------------------------------
def _get_profile_embedding(profile: dict) -> Optional[list[float]]:
    if not AOAI_ENDPOINT or not AOAI_KEY:
        logger.info("No AOAI credentials — skipping semantic embedding")
        return None
    try:
        from openai import AzureOpenAI
        import numpy as np

        emb_model = os.environ.get("AOAI_EMBEDDING_MODEL", "text-embedding-3-small")
        client = AzureOpenAI(
            azure_endpoint=AOAI_ENDPOINT, api_key=AOAI_KEY, api_version="2024-02-01"
        )
        # Build profile text from all keywords
        all_text = " ".join(
            kw
            for unit in profile.get("matching_units", [])
            for kw in unit.get("keywords_plan", []) + unit.get("keywords_expansion", [])
        )
        resp = client.embeddings.create(input=[all_text], model=emb_model)
        vec = resp.data[0].embedding
        logger.info("Got profile embedding (dim=%d)", len(vec))
        return vec
    except Exception as e:
        logger.warning("Embedding failed: %s — continuing without semantic scoring", e)
        return None


# ---------------------------------------------------------------------------
# AI summary generation (optional — requires Azure OpenAI)
# ---------------------------------------------------------------------------
def _generate_summary(opp: dict, profile: dict) -> str:
    """Generate a 2-3 sentence AI summary for one opportunity."""
    if not AOAI_ENDPOINT or not AOAI_KEY:
        return opp.get("synopsis", "")[:500]
    try:
        from openai import AzureOpenAI

        chat_model = os.environ.get("AOAI_CHAT_MODEL", "gpt-4.1-mini")
        client = AzureOpenAI(
            azure_endpoint=AOAI_ENDPOINT, api_key=AOAI_KEY, api_version="2024-02-01"
        )
        prompt_path = Path("prompts/v1.txt")
        if prompt_path.exists():
            system_prompt = prompt_path.read_text()
        else:
            system_prompt = (
                "You are a grants analyst helping NCDOT. Write 2-3 clear sentences about "
                "this grant opportunity. Include: what it funds, the award range, and why "
                "NCDOT is a strong applicant. Never invent facts. If information is not "
                "stated in the source, say 'not stated'."
            )

        user_msg = (
            f"Title: {opp.get('title', '')}\n"
            f"Funder: {opp.get('funder', '')}\n"
            f"Synopsis: {opp.get('synopsis', '')[:800]}\n"
            f"Eligibility: {opp.get('eligibility_note', '')[:200]}\n"
            f"Max award: {opp.get('max_award_usd', 'not stated')}\n"
            f"Close date: {opp.get('close_date', 'not stated')}"
        )

        resp = client.chat.completions.create(
            model=chat_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=200,
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("Summary generation failed for %s: %s", opp.get("id", ""), e)
        return opp.get("synopsis", "")[:500]


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run(account_id: str = PROFILE_ID) -> dict:
    run_start = datetime.now(timezone.utc)
    run_log: dict = {
        "account_id": account_id,
        "started_at": run_start.isoformat(),
        "status": "running",
        "steps": {},
    }

    # Step 1: Load profile
    logger.info("=== Step 1: Load profile [%s] ===", account_id)
    profile = None
    blob_profile = _read_blob(f"profiles/{account_id}.json")
    if blob_profile:
        profile = blob_profile
        logger.info("Loaded profile from blob")
    elif Path(PROFILE_PATH).exists():
        profile = load_profile(PROFILE_PATH)
        logger.info("Loaded profile from local file: %s", PROFILE_PATH)
    else:
        logger.error("No profile found for %s", account_id)
        run_log["status"] = "failed"
        run_log["error"] = f"Profile not found: {account_id}"
        return run_log

    run_log["steps"]["load_profile"] = "ok"

    # Step 2: Ingest
    logger.info("=== Step 2: Ingest from Grants.gov ===")
    t0 = time.time()
    raw_opps = ingest_for_profile(profile, fetch_details=False)
    run_log["steps"]["ingest"] = {
        "raw_count": len(raw_opps),
        "elapsed_s": round(time.time() - t0, 1),
    }
    logger.info("Ingest: %d raw opportunities in %.1fs", len(raw_opps), time.time() - t0)

    # Step 3: Normalize
    logger.info("=== Step 3: Normalize ===")
    normed = normalize_all(raw_opps)
    run_log["steps"]["normalize"] = {"normalized_count": len(normed)}

    if not normed:
        logger.warning("No opportunities after normalization — writing empty result")

    # Step 4: Score
    logger.info("=== Step 4: Score ===")
    profile_embedding = _get_profile_embedding(profile)
    mode = "full" if profile_embedding else "keyword_fallback"
    scored = score_all(normed, profile, profile_embedding, mode)
    run_log["steps"]["score"] = {"scored_count": len(scored), "mode": mode}
    logger.info("Scored %d opportunities (mode=%s)", len(scored), mode)

    # Step 4b: AI summaries for top-N (only if AOAI available)
    if AOAI_ENDPOINT and AOAI_KEY:
        logger.info("=== Step 4b: AI summaries for top 20 ===")
        for opp in scored[:20]:
            if not opp.get("summary"):
                opp["summary"] = _generate_summary(opp, profile)
                time.sleep(0.3)  # rate limit

    # Step 5: Write results
    logger.info("=== Step 5: Write results ===")
    result_obj = {
        "account_id": account_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "profile_review_status": profile.get("review", {}).get("status", "ai-draft"),
        "items": scored,
    }

    blob_path = f"scored/{account_id}/latest.json"
    written_to_blob = _write_blob(blob_path, result_obj)
    if not written_to_blob:
        # Fallback to local
        _write_local(f"scored_{account_id}_latest.json", result_obj)
    run_log["steps"]["write_results"] = {
        "destination": "blob" if written_to_blob else "local",
        "item_count": len(scored),
    }

    # Step 6: Write run log
    run_end = datetime.now(timezone.utc)
    elapsed = (run_end - run_start).total_seconds()
    run_log["status"] = "success"
    run_log["finished_at"] = run_end.isoformat()
    run_log["elapsed_s"] = round(elapsed, 1)
    logger.info("=== Pipeline complete in %.1fs ===", elapsed)

    ts = run_start.strftime("%Y%m%d-%H%M%S")
    run_blob = f"runs/{account_id}/{ts}.json"
    written = _write_blob(run_blob, run_log)
    if not written:
        _write_local(f"runs_{account_id}_{ts}.json", run_log)

    return run_log


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    account_id = sys.argv[1] if len(sys.argv) > 1 else PROFILE_ID
    result = run(account_id)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("status") == "success" else 1)
