"""
job.py — daily refresh pipeline  (MODE=job)
--------------------------------------------
Runs when the container starts with MODE=job.
Orchestrates: ingest → normalize → score → summarize → run log.

Usage (local test):
    MODE=job STORAGE_CONN=... AOAI_ENDPOINT=... AOAI_KEY=... python job.py

    # Offline / demo fallback (no live HTTP calls):
    MODE=job NSF_OFFLINE=true STORAGE_CONN=... python job.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

from azure.storage.blob import ContainerClient

from scrapers.nsf import ingest_nsf

# Import the existing Grants.gov ingest if it exists, else stub
try:
    from scrapers.grants_gov import ingest_grants_gov
except ImportError:
    def ingest_grants_gov(blob_client):
        logger.warning("scrapers/grants_gov.py not found — skipping Grants.gov ingest")
        return {"source": "grants.gov", "count": 0, "skipped": True}

from pipeline.normalize import normalize_all

# Optional — only imported if the scoring module has been written
try:
    from pipeline.score import score_and_rank
    from pipeline.summarize import summarize_top_n
    HAS_SCORING = True
except ImportError:
    HAS_SCORING = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("job")


def run_pipeline(agency: str, blob_client, aoai_client=None) -> dict:
    """
    Full pipeline for one agency.
    Returns a run-log dict suitable for writing to runs/{timestamp}.json.
    """
    log: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "agency":     agency,
        "stages":     {},
    }

    # ------------------------------------------------------------------
    # Stage 1: Ingest — add new sources here, one line each
    # ------------------------------------------------------------------
    logger.info("Stage 1: ingest")
    ingest_results = []

    ingest_results.append(ingest_grants_gov(blob_client))
    ingest_results.append(ingest_nsf(blob_client))
    # ingest_results.append(ingest_nih(blob_client))   # future
    # ingest_results.append(ingest_usda(blob_client))  # future

    log["stages"]["ingest"] = ingest_results
    total_ingested = sum(r.get("count", 0) for r in ingest_results)
    logger.info("Ingest complete: %d total raw records", total_ingested)

    # ------------------------------------------------------------------
    # Stage 2: Normalize — reads all raw/{today}/*.json from Blob
    # ------------------------------------------------------------------
    logger.info("Stage 2: normalize")
    try:
        opportunities = normalize_all(blob_client)
        log["stages"]["normalize"] = {"count": len(opportunities)}
        logger.info("Normalize complete: %d active opportunities", len(opportunities))
    except Exception as exc:
        logger.error("Normalize failed: %s", exc)
        log["stages"]["normalize"] = {"error": str(exc)}
        log["status"] = "failed"
        log["finished_at"] = datetime.now(timezone.utc).isoformat()
        return log

    # ------------------------------------------------------------------
    # Stage 3 & 4: Score + Summarize (skipped gracefully if not built yet)
    # ------------------------------------------------------------------
    if HAS_SCORING and aoai_client:
        logger.info("Stage 3: score and rank")
        try:
            scored = score_and_rank(opportunities, agency, blob_client, aoai_client)
            log["stages"]["score"] = {"count": len(scored)}
            logger.info("Scoring complete: %d scored opportunities", len(scored))
        except Exception as exc:
            logger.error("Scoring failed: %s", exc)
            log["stages"]["score"] = {"error": str(exc)}
            scored = []

        logger.info("Stage 4: summarize top 15")
        try:
            summarize_top_n(scored[:15], agency, blob_client, aoai_client)
            log["stages"]["summarize"] = {"count": min(15, len(scored))}
        except Exception as exc:
            logger.error("Summarize failed: %s", exc)
            log["stages"]["summarize"] = {"error": str(exc)}
    else:
        reason = "scoring module not built" if not HAS_SCORING else "no AOAI client"
        logger.info("Skipping score/summarize: %s", reason)
        log["stages"]["score"]     = {"skipped": reason}
        log["stages"]["summarize"] = {"skipped": reason}

    # ------------------------------------------------------------------
    # Write run log to Blob
    # ------------------------------------------------------------------
    log["status"] = "succeeded"
    log["finished_at"] = datetime.now(timezone.utc).isoformat()

    blob_client.upload_blob(
        name=f"runs/{log['started_at']}.json",
        data=json.dumps(log, indent=2, default=str),
        overwrite=True,
    )
    logger.info("Run log written. Pipeline finished.")
    return log


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    conn_str = os.environ.get("STORAGE_CONN")
    if not conn_str:
        logger.error("STORAGE_CONN environment variable is required")
        sys.exit(1)

    agency = os.environ.get("AGENCY", "NC DEQ")

    blob_client = ContainerClient.from_connection_string(conn_str, container_name="ffm-data")

    # Wire up Azure OpenAI client if credentials are present
    aoai_client = None
    aoai_endpoint = os.environ.get("AOAI_ENDPOINT")
    aoai_key      = os.environ.get("AOAI_KEY")
    if aoai_endpoint and aoai_key:
        try:
            from openai import AzureOpenAI
            aoai_client = AzureOpenAI(
                azure_endpoint=aoai_endpoint,
                api_key=aoai_key,
                api_version="2024-02-01",
            )
        except ImportError:
            logger.warning("openai package not installed — AOAI features disabled")

    result = run_pipeline(agency, blob_client, aoai_client)

    # Exit non-zero on failure so Azure marks the job run as Failed
    if result.get("status") != "succeeded":
        logger.error("Pipeline did not succeed: %s", result)
        sys.exit(1)
