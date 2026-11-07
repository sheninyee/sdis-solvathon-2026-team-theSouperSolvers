"""main.py — Federal Funds Matchmaker web server (MODE=app)."""
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
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
    # Try blob first
    obj = _read_blob(f"scored/{account_id}/latest.json")
    if not obj:
        # Local fallback
        obj = _read_local(f"scored_{account_id}_latest.json")
    if not obj:
        # Hard fallback: empty
        obj = {
            "account_id": account_id,
            "generated_at": None,
            "mode": "keyword_fallback",
            "items": [],
            "profile_review_status": "ai-draft",
        }
    return obj


@app.get("/api/run-status")
def run_status():
    """Return the most recent job run log from blob."""
    import re
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
    """Return seeded accounts (NCDOT pre-seeded)."""
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
    return {"accounts": [ncdot]}


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
@app.get("/api/export")
def export_csv(account_id: str = "ncdot"):
    opp_data = get_opportunities(account_id)
    decisions = list_decisions(account_id)
    items = opp_data.get("items", [])

    # Columns per UI_EDIT_LIST.md section 8
    export_date = __import__("datetime").date.today().isoformat()
    fname = f"ffm_{account_id}_{export_date}.csv"
    header = "rank,title,funder,opp_number,status,score,confidence,matched_goal,eligibility,effort,due_date,days_left,award_ceiling,decision,note,link"
    rows = [header]
    for item in items:
        d = decisions.get(item["id"], {})
        def q(s): return f'"{str(s or "").replace(chr(34), chr(39))}"'
        matched = ""
        if item.get("matched_units"):
            mu = item["matched_units"]
            matched = mu[0].get("id", mu[0]) if isinstance(mu[0], dict) else mu[0]
        row = [
            str(item.get("rank", "")),
            q(item.get("title", "")),
            q(item.get("funder", "")),
            q(item.get("number", item.get("cfda", ""))),
            item.get("status", ""),
            str(item.get("score", "")),
            item.get("confidence", ""),
            q(matched),
            item.get("eligibility_verdict", ""),
            item.get("effort", {}).get("level", "") if isinstance(item.get("effort"), dict) else str(item.get("effort", "")),
            item.get("close_date", "") or "",
            str(item.get("days_to_close", "")) if item.get("days_to_close") is not None else "",
            str(int(item.get("max_award_usd") or 0)) if item.get("max_award_usd") else "",
            d.get("decision", ""),
            q(d.get("note", "")),
            item.get("url", ""),
        ]
        rows.append(",".join(row))

    csv_content = "\n".join(rows)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
@app.get("/{path:path}")
def spa(path: str = ""):
    return FileResponse(str(STATIC_DIR / "index.html"))
