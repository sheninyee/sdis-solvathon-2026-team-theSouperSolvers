"""main.py — Federal Funds Matchmaker web server (MODE=app)."""
import hashlib
import json
import logging
import os
import re
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory caches (per process lifetime)
_ai_profile_cache: dict = {}   # account_id → profile dict
_analyze_cache: dict = {}       # opp_id → grant analysis dict
_fit_cache: dict = {}           # "account_id:opp_id" → fit assessment dict

PROFILES_DIR = Path(__file__).parent / "profiles"


def _load_profile_file(account_id: str) -> Optional[dict]:
    """Load a profile JSON from the profiles/ directory, if it exists."""
    path = PROFILES_DIR / f"{account_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            logger.warning("Failed to load profile %s: %s", path, e)
    return None


def _generate_ai_profile(account_id: str, org_name: str) -> dict:
    """
    Call GPT-4.1-mini to produce a reasonable grant-matching profile
    for an organisation that doesn't have a hand-crafted profile file.
    Result is cached in _ai_profile_cache for the process lifetime.
    """
    if account_id in _ai_profile_cache:
        return _ai_profile_cache[account_id]

    endpoint = os.getenv("AOAI_ENDPOINT", "").rstrip("/")
    key = os.getenv("AOAI_KEY", "")
    model = os.getenv("AOAI_CHAT_MODEL", "chat")

    if not endpoint or not key:
        logger.warning("No AOAI credentials — returning stub profile for %s", account_id)
        return _empty_profile(account_id, org_name)

    prompt = (
        f'You are a grants intelligence assistant.\n\n'
        f'Generate a JSON profile for "{org_name}" (account_id: {account_id}) '
        f'describing its strategic priorities for federal grant matching.\n\n'
        f'Return ONLY valid JSON, no markdown fences, matching this exact schema:\n'
        f'{{\n'
        f'  "account_id": "{account_id}",\n'
        f'  "org_name": "{org_name}",\n'
        f'  "profile_review_status": "ai-draft",\n'
        f'  "matching_units": [\n'
        f'    {{\n'
        f'      "id": "short_snake_case_id",\n'
        f'      "title": "Strategic Goal Title",\n'
        f'      "keywords_plan": ["keyword1", "keyword2"],\n'
        f'      "keywords_expansion": ["related1", "related2"]\n'
        f'    }}\n'
        f'  ]\n'
        f'}}\n\n'
        f'Rules:\n'
        f'- 4-8 matching_units representing distinct strategic goal areas\n'
        f'- 6-12 keywords_plan and 8-15 keywords_expansion each\n'
        f'- Keywords should be grant-relevant terms that appear in federal RFAs\n'
        f'- Base goals on publicly known information about this type of organisation\n'
        f'- Return only the JSON object, nothing else\n'
    )

    url = (
        f"{endpoint}/openai/deployments/{model}"
        f"/chat/completions?api-version=2024-12-01-preview"
    )
    payload = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000,
        "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "api-key": key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            text = body["choices"][0]["message"]["content"]
            text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
            profile = json.loads(text)
            _ai_profile_cache[account_id] = profile
            logger.info(
                "AI-generated profile for %s (%d units)",
                account_id,
                len(profile.get("matching_units", [])),
            )
            return profile
    except Exception as e:
        logger.error("AI profile generation failed for %s: %s", account_id, e)
        return _empty_profile(account_id, org_name)


def _empty_profile(account_id: str, org_name: str) -> dict:
    return {
        "account_id": account_id,
        "org_name": org_name,
        "profile_review_status": "needs-setup",
        "matching_units": [],
    }

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STORAGE_CONN = os.environ.get("STORAGE_CONN", "")
CONTAINER = "ffm-data"
DATA_DIR = Path("data/sample")
STATIC_DIR = Path("static")
APP_ACCESS_CODE = os.environ.get("APP_ACCESS_CODE", "")  # outer gate; empty = open

app = FastAPI(title="Federal Funds Matchmaker")

# ---------------------------------------------------------------------------
# Blob helpers (optional — falls back to local files gracefully)
# ---------------------------------------------------------------------------
def _blob_client(blob_path: str):
    if not STORAGE_CONN:
        return None
    try:
        from azure.storage.blob import BlobServiceClient
        svc = BlobServiceClient.from_connection_string(STORAGE_CONN)
        return svc.get_blob_client(CONTAINER, blob_path)
    except Exception:
        return None


def _read_blob(blob_path: str) -> Optional[dict]:
    bc = _blob_client(blob_path)
    if bc:
        try:
            data = bc.download_blob().readall()
            return json.loads(data)
        except Exception:
            pass
    return None


def _write_blob(blob_path: str, obj: dict) -> bool:
    bc = _blob_client(blob_path)
    if bc:
        try:
            bc.upload_blob(json.dumps(obj, indent=2), overwrite=True)
            return True
        except Exception:
            pass
    return False


def _read_local(rel_path: str) -> Optional[dict]:
    p = DATA_DIR / rel_path
    if p.exists():
        return json.loads(p.read_text())
    return None


def _write_local(rel_path: str, obj: dict):
    p = DATA_DIR / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2))

# ---------------------------------------------------------------------------
# Auth helpers (demo-grade)
# ---------------------------------------------------------------------------
def _hash_secret(secret: str) -> str:
    return hashlib.scrypt(
        secret.encode(), salt=b"ffm-demo-salt", n=16384, r=8, p=1
    ).hex()


def _verify_account_ownership(account_id: str, user_id: str) -> bool:
    """Server-side: confirm user owns account before any operation."""
    acct = _read_blob(f"accounts/{account_id}.json") or _read_local(f"accounts/{account_id}.json")
    if not acct:
        return False
    return acct.get("owner_user_id") == user_id

# ---------------------------------------------------------------------------
# Access-code middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def access_gate(request: Request, call_next):
    if APP_ACCESS_CODE and request.url.path not in ("/health",):
        code = request.cookies.get("ffm_access") or request.headers.get("X-Access-Code", "")
        if code != APP_ACCESS_CODE:
            if request.url.path.startswith("/api/"):
                return JSONResponse({"error": "access_required"}, status_code=401)
            # For the browser, let the page handle it (JS will show the gate)
    return await call_next(request)

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}

# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------
@app.get("/api/opportunities")
def get_opportunities(account_id: str = "ncdot"):
    # 1. Try account-specific blob
    obj = _read_blob(f"scored/{account_id}/latest.json")
    if not obj:
        # 2. Local account-specific fallback
        obj = _read_local(f"scored_{account_id}_latest.json")
    if not obj and account_id != "ncdot":
        # 3. Shared ncdot dataset — all accounts see the same 62 ranked grants
        obj = _read_blob("scored/ncdot/latest.json")
    if not obj:
        # 4. Local ncdot fallback
        obj = _read_local("scored_ncdot_latest.json")
    if not obj:
        obj = {
            "account_id": account_id,
            "generated_at": None,
            "mode": "keyword_fallback",
            "items": [],
            "profile_review_status": "ai-draft",
        }
    # Stamp actual account_id so decisions save to the right namespace
    obj["account_id"] = account_id
    return obj


@app.get("/api/profile")
def get_profile(account_id: str = Query("ncdot")):
    """
    Return unit_id → {title, keywords} map for the given account.
    Priority:
      1. profiles/{account_id}.json  (hand-crafted or PDF-extracted)
      2. AI-generated profile (GPT-4.1-mini assumption, cached in memory)
      3. Fall back to ncdot profile
    """
    # 1. Try exact hand-crafted match
    profile = _load_profile_file(account_id)

    # 2. AI assumption for unknown accounts
    if not profile or not profile.get("matching_units"):
        org_name = account_id.upper().replace("_", " ").replace("-", " ")
        profile = _generate_ai_profile(account_id, org_name)

    # 3. Hard fallback to ncdot
    if not profile or not profile.get("matching_units"):
        profile = _load_profile_file("ncdot") or {"matching_units": []}

    units = [
        {
            "id": u["id"],
            "title": u["title"],
            "keywords": list({*u.get("keywords_plan", []), *u.get("keywords_expansion", [])}),
        }
        for u in profile.get("matching_units", [])
    ]
    return {
        "account_id": account_id,
        "org_name": profile.get("org_name", account_id),
        "profile_review_status": profile.get("profile_review_status", "ok"),
        "priority": profile.get("priority", "balanced"),  # budget | time | balanced
        "units": units,
    }


# ---------------------------------------------------------------------------
# Grant analysis (stage 1) — grant-only structured breakdown
# ---------------------------------------------------------------------------
def _call_chat(messages: list, max_tokens: int = 1500) -> Optional[str]:
    """Call Azure OpenAI chat completion; returns content string or None."""
    endpoint = os.getenv("AOAI_ENDPOINT", "").rstrip("/")
    key = os.getenv("AOAI_KEY", "")
    model = os.getenv("AOAI_CHAT_MODEL", "chat")
    if not endpoint or not key:
        return None
    url = (
        f"{endpoint}/openai/deployments/{model}"
        f"/chat/completions?api-version=2024-12-01-preview"
    )
    payload = json.dumps({
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "api-key": key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            text = body["choices"][0]["message"]["content"]
            text = re.sub(r"```(?:json)?", "", text).strip().strip("`").strip()
            return text
    except Exception as e:
        logger.error("Chat call failed: %s", e)
        return None


def _grant_text(item: dict) -> str:
    """Build a readable text block for a grant opportunity dict."""
    parts = []
    if item.get("title"):
        parts.append(f"Title: {item['title']}")
    if item.get("funder"):
        parts.append(f"Funder: {item['funder']}")
    if item.get("description"):
        parts.append(f"Description: {item['description']}")
    if item.get("summary"):
        parts.append(f"Summary: {item['summary']}")
    if item.get("eligibility"):
        parts.append(f"Eligibility: {item['eligibility']}")
    if item.get("url"):
        parts.append(f"URL: {item['url']}")
    for field in ("award_ceiling", "award_floor", "max_award"):
        if item.get(field):
            parts.append(f"Award ceiling: {item[field]}")
            break
    if item.get("close_date"):
        parts.append(f"Close date: {item['close_date']}")
    if item.get("effort"):
        lvl = item["effort"].get("level", "")
        note = item["effort"].get("note", "")
        if lvl or note:
            parts.append(f"Application effort: {lvl} {note}".strip())
    if item.get("competition_level"):
        parts.append(f"Competition level: {item['competition_level']}")
    return "\n".join(parts)


@app.get("/api/analyze")
def analyze_grant(opp_id: str, account_id: str = "ncdot"):
    """
    Stage 1: Return a structured breakdown of a single grant — grant text only,
    no org-specific content. Cached per opp_id.
    """
    if opp_id in _analyze_cache:
        return {**_analyze_cache[opp_id], "cached": True}

    # Find the grant item
    opp_data = get_opportunities(account_id)
    item = next((x for x in opp_data.get("items", []) if x.get("id") == opp_id), None)
    if not item:
        raise HTTPException(404, f"Opportunity '{opp_id}' not found")

    grant_text = _grant_text(item)

    prompt = f"""You are a federal grants analyst. Analyze the grant opportunity below and return ONLY valid JSON.

GRANT TEXT:
{grant_text}

Return JSON matching this exact schema:
{{
  "summary": "2-3 sentence plain-language summary of what this grant funds",
  "eligibility_requirements": [
    "Specific thing an applicant must have or demonstrate to be eligible"
  ],
  "pros": [
    "Positive aspect — e.g. high award, low competition, broad eligibility"
  ],
  "cons": [
    "Risk or drawback — e.g. very competitive, narrow scope, short timeline"
  ]
}}

Rules:
- eligibility_requirements: 3-7 items, each a concrete requirement from the grant text. If not stated, write "Not stated in source text."
- pros: 2-5 items based only on grant text
- cons: 2-4 items based only on grant text
- Never invent facts. Write "Not stated in source text" when the grant does not say something.
- Return only the JSON object, no markdown."""

    result_text = _call_chat([{"role": "user", "content": prompt}], max_tokens=1200)
    if not result_text:
        result = {
            "summary": "Analysis unavailable — AI service not configured.",
            "eligibility_requirements": ["Not stated in source text."],
            "pros": [],
            "cons": [],
        }
    else:
        try:
            result = json.loads(result_text)
        except Exception:
            result = {
                "summary": result_text[:500],
                "eligibility_requirements": ["Not stated in source text."],
                "pros": [],
                "cons": [],
            }

    result["opp_id"] = opp_id
    _analyze_cache[opp_id] = result
    logger.info("Analyzed grant %s", opp_id)
    return {**result, "cached": False}


# ---------------------------------------------------------------------------
# Fit assessment (stage 2) — grant × org profile
# ---------------------------------------------------------------------------
@app.get("/api/fit")
def assess_fit(opp_id: str, account_id: str = "ncdot"):
    """
    Stage 2: Cross-reference grant analysis with org strategic profile.
    Returns why_relevant, recommendation, fit_notes. Cached per account_id:opp_id.
    """
    cache_key = f"{account_id}:{opp_id}"
    if cache_key in _fit_cache:
        return {**_fit_cache[cache_key], "cached": True}

    # Get grant analysis (reuse cache or run fresh)
    analysis = _analyze_cache.get(opp_id)
    if not analysis:
        # Run analyze inline (without HTTP overhead)
        opp_data = get_opportunities(account_id)
        item = next((x for x in opp_data.get("items", []) if x.get("id") == opp_id), None)
        if not item:
            raise HTTPException(404, f"Opportunity '{opp_id}' not found")
        grant_text = _grant_text(item)
        analyze_grant(opp_id, account_id)  # populates _analyze_cache
        analysis = _analyze_cache.get(opp_id, {})

    # Get org profile
    profile_resp = get_profile(account_id)
    priority = profile_resp.get("priority", "balanced")
    org_name = profile_resp.get("org_name", account_id)
    units = profile_resp.get("units", [])

    units_text = "\n".join(
        f"- {u['title']}: {', '.join(u.get('keywords', [])[:10])}"
        for u in units[:8]
    )

    priority_guidance = {
        "budget": "The organization prioritizes grants with the highest award amounts.",
        "time": "The organization prioritizes grants with the easiest / fastest application process.",
        "balanced": "The organization balances award size and application effort equally.",
    }.get(priority, "The organization balances award size and application effort equally.")

    prompt = f"""You are a grants intelligence advisor helping {org_name}, a state government agency.

Write a 3-part briefing for a program manager reviewing this grant. Use only the provided data — do not invent or infer facts not present in the source. If a field is missing, write "Not specified in the available notice."

GRANT DATA:
{json.dumps(analysis, indent=2)}

AGENCY STRATEGIC GOALS:
{units_text if units_text else "Not provided — infer from agency statutory mission."}

AGENCY PRIORITY: {priority_guidance}

Return ONLY valid JSON matching this exact schema:
{{
  "purpose": "1-2 sentences: what problem does this grant address and what does it fund?",
  "fit": "1-2 sentences: what specific agency priority, program, or activity could this support? Name something concrete; if none, name the closest relevant agency goal or mission area.",
  "watch_out": "1-2 sentences on a genuinely noteworthy barrier — cost-share obligation, cap on number of awards, narrow eligibility, or very short turnaround. Set to null if nothing stands out.",
  "recommendation": "pursue | maybe | pass"
}}

Rules:
- purpose and fit: draw only from the grant data provided. If a field is missing, write "Not specified in the available notice."
- watch_out: include ONLY when there is a real, specific concern worth flagging. Set to null otherwise — do not pad with generic advice.
- recommendation: "pursue" = strong fit + matches agency priority; "maybe" = partial fit or uncertainty; "pass" = poor fit.
- Tone: plain English for a program manager, not a grants officer. No jargon.
- Return only the JSON object, no markdown, no explanation."""

    result_text = _call_chat([{"role": "user", "content": prompt}], max_tokens=600)
    if not result_text:
        result = {
            "purpose": "Fit assessment unavailable — AI service not configured.",
            "fit": "Review the grant manually against your strategic goals.",
            "watch_out": None,
            "recommendation": "maybe",
        }
    else:
        try:
            result = json.loads(result_text)
        except Exception:
            result = {
                "purpose": result_text[:400],
                "fit": "",
                "watch_out": None,
                "recommendation": "maybe",
            }

    result["opp_id"] = opp_id
    result["account_id"] = account_id
    _fit_cache[cache_key] = result
    logger.info("Fit assessed %s for %s", opp_id, account_id)
    return {**result, "cached": False}




# ---------------------------------------------------------------------------
# Project Match (new feature) — analyze uploaded docs + find matching grants
# ---------------------------------------------------------------------------
import io

def _extract_text_from_upload(filename: str, content: bytes) -> str:
    """Extract plain text from various file types. Returns '' on failure."""
    fn = filename.lower()
    try:
        if fn.endswith((".txt", ".md", ".py", ".js", ".csv")):
            return content.decode("utf-8", errors="replace")[:30000]
        elif fn.endswith(".pdf"):
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(stream=content, filetype="pdf")
                return "\n".join(page.get_text() for page in doc)[:30000]
            except ImportError:
                try:
                    from pdfminer.high_level import extract_text_to_fp
                    from pdfminer.layout import LAParams
                    buf = io.StringIO()
                    extract_text_to_fp(io.BytesIO(content), buf, laparams=LAParams())
                    return buf.getvalue()[:30000]
                except Exception:
                    return ""
        elif fn.endswith(".docx"):
            import zipfile, xml.etree.ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                with z.open("word/document.xml") as f:
                    tree = ET.parse(f)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            texts = [n.text for n in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if n.text]
            return " ".join(texts)[:30000]
        elif fn.endswith((".xlsx", ".xls")):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                rows = []
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        line = "\t".join(str(c) if c is not None else "" for c in row)
                        if line.strip():
                            rows.append(line)
                return "\n".join(rows)[:30000]
            except Exception:
                return ""
        else:
            # Try plain text decode
            try:
                return content.decode("utf-8", errors="replace")[:10000]
            except Exception:
                return ""
    except Exception as e:
        logger.warning("Text extraction failed for %s: %s", filename, e)
        return ""


@app.post("/api/project-match")
async def project_match(
    request: Request,
    account_id: str = "ncdot",
):
    """
    Accepts multipart form with optional files and optional pasted_text.
    Extracts project text, generates a project summary, then ranks
    the account's current grant opportunities by relevance.
    """
    try:
        form = await request.form()
    except Exception as form_err:
        logger.error("project_match form parse failed: %s\n%s", form_err, traceback.format_exc())
        raise HTTPException(422, f"Form parse error: {form_err}")
    pasted_text = form.get("pasted_text", "") or ""

    # Extract text from each uploaded file
    file_texts = []
    for key, value in form.multi_items():
        if key == "files" and hasattr(value, "filename"):
            try:
                raw = await value.read()
                extracted = _extract_text_from_upload(value.filename, raw)
                if extracted.strip():
                    file_texts.append(f"[File: {value.filename}]\n{extracted}")
            except Exception as file_err:
                logger.warning("project_match file read failed %s: %s", value.filename, file_err)

    all_text = "\n\n---\n\n".join(filter(None, [pasted_text] + file_texts))
    if not all_text.strip():
        raise HTTPException(400, "No text content provided. Add files or paste text.")

    # Truncate to stay within token budget for the LLM call
    all_text = all_text[:25000]

    # Get current grants list (abbreviated for the prompt)
    opp_data = get_opportunities(account_id)
    items = opp_data.get("items", [])[:100]
    grants_index = "\n".join(
        f"{i+1}. [{item.get('id','')}] {item.get('title','')} | {item.get('funder','')} | score={item.get('score','')}"
        for i, item in enumerate(items)
    )

    prompt = f"""You are a federal grants advisor. A government agency has shared project documents below.

PROJECT DOCUMENTS:
{all_text}

AVAILABLE GRANTS (id | title | funder | relevance score):
{grants_index}

Your tasks:
1. Summarize the project described in the documents (2-4 sentences).
2. Identify the top 10-12 most relevant grants from the list above.

Return ONLY valid JSON:
{{
  "project_summary": "2-4 sentence summary of what the project is about and what it needs",
  "project_keywords": ["keyword1", "keyword2"],
  "matches": [
    {{
      "opp_id": "grant_id_from_list",
      "title": "grant title",
      "relevance_score": 0-100,
      "reason": "1-2 sentences explaining why this grant fits the project, citing both project and grant specifics"
    }}
  ]
}}

Rules:
- matches must use opp_id values exactly from the list above (the bracketed IDs)
- Include 10-12 matches, sorted by relevance_score descending
- Never invent grants not in the list
- reason must reference specific details from both the project text and the grant
- Return only the JSON object"""

    result_text = _call_chat([{"role": "user", "content": prompt}], max_tokens=2000)

    result = None
    if result_text:
        # Strip markdown code fences if AI wraps JSON in ```json ... ```
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", result_text.strip())
        cleaned = re.sub(r"```$", "", cleaned.strip())
        try:
            result = json.loads(cleaned)
        except Exception:
            logger.warning("AI returned invalid JSON, using keyword fallback. AI output: %s", result_text[:200])

    # Keyword fallback when AI unavailable or returned bad JSON
    if not result:
        project_words = set(re.findall(r"\b[a-z]{4,}\b", all_text.lower()))
        keyword_matches = []
        for item in items:
            item_text = f"{item.get('title','')} {item.get('synopsis','') or ''} {item.get('description','') or ''}".lower()
            item_words = set(re.findall(r"\b[a-z]{4,}\b", item_text))
            overlap = len(project_words & item_words)
            if overlap > 0:
                keyword_matches.append((overlap, item))
        keyword_matches.sort(key=lambda x: -x[0])
        fallback_matches = []
        for overlap, item in keyword_matches[:12]:
            score = min(100, overlap * 5)
            fallback_matches.append({
                "opp_id": item.get("id"),
                "title": item.get("title"),
                "relevance_score": score,
                "reason": f"Keyword overlap ({overlap} shared terms) between project text and grant description.",
            })
        result = {
            "project_summary": "Project summary unavailable (AI offline — keyword matching used).",
            "project_keywords": list(project_words)[:10],
            "matches": fallback_matches,
        }

    # Enrich matches with full item data (eligibility, score breakdown, dates)
    try:
        items_by_id = {item.get("id"): item for item in opp_data.get("items", [])}
        for m in result.get("matches", []):
            full = items_by_id.get(m.get("opp_id"), {})
            m["score"] = full.get("score")
            m["confidence"] = full.get("confidence")
            m["close_date"] = full.get("close_date")
            m["days_to_close"] = full.get("days_to_close")
            m["url"] = full.get("url")
            m["effort"] = full.get("effort", {})
            m["funder"] = full.get("funder") or m.get("funder")
            m["eligibility_verdict"] = full.get("eligibility_verdict")
            m["eligibility_reason"] = full.get("eligibility_reason")
            m["score_breakdown"] = full.get("score_breakdown", {})
    except Exception as enrich_err:
        logger.error("project_match enrich failed: %s\n%s", enrich_err, traceback.format_exc())

    return result


@app.get("/api/run-status")
def run_status():
    """Return the most recent job run log from blob."""
    if STORAGE_CONN:
        try:
            from azure.storage.blob import BlobServiceClient
            svc = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cc = svc.get_container_client(CONTAINER)
            blobs = [b.name for b in cc.list_blobs(name_starts_with="runs/")]
            if blobs:
                latest = sorted(blobs)[-1]
                bc = svc.get_blob_client(CONTAINER, latest)
                return json.loads(bc.download_blob().readall())
        except Exception:
            pass
    return {"status": "no_run_found"}

# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
@app.get("/api/decisions")
def list_decisions(account_id: str = "ncdot"):
    """Return all decisions for an account as {opp_id: decision_obj}."""
    decisions = {}
    if STORAGE_CONN:
        try:
            from azure.storage.blob import BlobServiceClient
            svc = BlobServiceClient.from_connection_string(STORAGE_CONN)
            cc = svc.get_container_client(CONTAINER)
            prefix = f"decisions/{account_id}/"
            for b in cc.list_blobs(name_starts_with=prefix):
                bc = svc.get_blob_client(CONTAINER, b.name)
                obj = json.loads(bc.download_blob().readall())
                decisions[obj.get("opp_id", b.name)] = obj
        except Exception:
            pass
    # Local fallback
    local_dir = DATA_DIR / f"decisions_{account_id}"
    if local_dir.exists():
        for f in local_dir.glob("*.json"):
            obj = json.loads(f.read_text())
            decisions[obj.get("opp_id", f.stem)] = obj
    return decisions


@app.post("/api/decisions/{opp_id}")
async def save_decision(opp_id: str, request: Request, account_id: str = "ncdot"):
    body = await request.json()
    decision = body.get("decision")  # pursue / maybe / pass
    note = body.get("note", "")
    user_id = body.get("user_id", "demo")

    if decision not in ("pursue", "maybe", "pass"):
        raise HTTPException(400, "decision must be pursue, maybe, or pass")

    obj = {
        "opp_id": opp_id,
        "account_id": account_id,
        "user_id": user_id,
        "decision": decision,
        "note": note,
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    blob_path = f"decisions/{account_id}/{opp_id}__{user_id}.json"
    written = _write_blob(blob_path, obj)
    if not written:
        # Local fallback
        _write_local(f"decisions_{account_id}/{opp_id}__{user_id}.json", obj)
    return {"saved": True, "decision": decision}


# ---------------------------------------------------------------------------
# Accounts (demo-grade)
# ---------------------------------------------------------------------------
@app.get("/api/accounts")
def list_accounts():
    """Return seeded accounts (NCDOT pre-seeded + any user-created ones)."""
    import datetime
    ncdot = _read_blob("accounts/ncdot.json") or _read_local("accounts_ncdot.json")
    if not ncdot:
        ncdot = {
            "account_id": "ncdot",
            "display_name": "NC Department of Transportation",
            "type": "department",
            "owner_user_id": "demo",
            "created_at": "2026-11-07T00:00:00Z",
            "profile_status": "ai-draft",
        }
    accounts = [ncdot]
    # Also load any user-created accounts from local profiles dir
    for p in PROFILES_DIR.glob("*.json"):
        if p.stem == "ncdot":
            continue
        try:
            data = json.loads(p.read_text())
            if "account_id" in data:
                accounts.append(data)
        except Exception:
            pass
    return {"accounts": accounts}


class CreateAccountRequest(BaseModel):
    display_name: str
    type: str = "department"


@app.post("/api/accounts")
def create_account(req: CreateAccountRequest):
    """Create a new department/division account (demo-grade, stored locally)."""
    import datetime
    if not req.display_name or not req.display_name.strip():
        raise HTTPException(status_code=422, detail="display_name is required")
    # Derive a stable account_id from the display name
    slug = re.sub(r"[^a-z0-9]+", "", req.display_name.lower())[:20] or "acct"
    account_id = slug
    # Avoid collision with existing accounts
    existing = set(p.stem for p in PROFILES_DIR.glob("*.json"))
    if account_id in existing:
        account_id = slug + "_" + str(len(existing))
    record = {
        "account_id": account_id,
        "display_name": req.display_name.strip(),
        "type": req.type,
        "owner_user_id": "demo",
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "profile_status": "new",
    }
    # Persist locally
    out_path = PROFILES_DIR / f"{account_id}.json"
    out_path.write_text(json.dumps(record, indent=2))
    logger.info("Created account %s (%s)", account_id, req.display_name)
    return record


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
@app.get("/api/export")
def export_csv(account_id: str = "ncdot"):
    opp_data = get_opportunities(account_id)
    decisions = list_decisions(account_id)
    items = opp_data.get("items", [])

    rows = ["rank,title,funder,score,confidence,effort,close_date,status,decision,note,url"]
    for item in items:
        d = decisions.get(item["id"], {})
        row = [
            str(item.get("rank", "")),
            f'"{item.get("title","").replace(chr(34), chr(39))}"',
            f'"{item.get("funder","")}"',
            str(item.get("score", "")),
            item.get("confidence", ""),
            item.get("effort", {}).get("level", ""),
            item.get("close_date", "") or "",
            item.get("status", ""),
            d.get("decision", ""),
            f'"{d.get("note","")}"',
            item.get("url", ""),
        ]
        rows.append(",".join(row))

    csv_content = "\n".join(rows)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ffm_{account_id}.csv"'},
    )


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
@app.get("/{path:path}")
def spa(path: str = ""):
    return FileResponse(str(STATIC_DIR / "index.html"))
