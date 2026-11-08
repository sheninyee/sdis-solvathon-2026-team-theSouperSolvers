"""ingest.py — Pull opportunities from Grants.gov API v2.

Grants.gov endpoint (no auth required):
  POST https://apply07.grants.gov/grantsws/rest/opportunities/search/
  or new v2: POST https://api.grants.gov/v2/api/opportunities/search (beta)

We use the stable v1 endpoint:
  POST https://apply07.grants.gov/grantsws/rest/opportunities/search/

Fields we care about (from the response):
  oppNumber, title, agencyCode, agencyName, openDate, closeDate, oppStatus,
  cfdaNumber, synopsis (from fetchOpportunity), eligibilities, awardCeiling, awardFloor
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GRANTS_GOV_SEARCH = "https://apply07.grants.gov/grantsws/rest/opportunities/search/"
GRANTS_GOV_DETAIL = "https://apply07.grants.gov/grantsws/rest/opportunities/oppdetails/"
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = [2, 5, 10]  # seconds between retries
MAX_PER_QUERY = 25  # Grants.gov returns max 25 per page; we page if needed


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _post_json(url: str, payload: dict, retries: int = MAX_RETRIES) -> dict:
    body = json.dumps(payload).encode()
    req = Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "FederalFundsMatchmaker/1.0 (academic prototype)",
        },
        method="POST",
    )
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
                return json.loads(raw)
        except HTTPError as e:
            if e.code in (429, 503) and attempt < retries - 1:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning("Rate limit/503 from Grants.gov — waiting %ds (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            raise
        except (URLError, json.JSONDecodeError) as e:
            if attempt < retries - 1:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning("Grants.gov error %s — retrying in %ds", e, wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Failed after {retries} attempts: {url}")


def _get_json(url: str, retries: int = MAX_RETRIES) -> dict:
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "FederalFundsMatchmaker/1.0 (academic prototype)",
        },
    )
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read())
        except (HTTPError, URLError) as e:
            if attempt < retries - 1:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                logger.warning("Grants.gov GET error %s — retrying %ds", e, wait)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Failed after {retries} attempts: {url}")


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search_opportunities(
    keyword_set: list[str],
    agency_codes: Optional[list[str]] = None,
    rows: int = MAX_PER_QUERY,
    start_record: int = 0,
) -> list[dict]:
    """
    Search Grants.gov for opportunities matching keywords.
    Returns list of raw opportunity dicts.
    """
    payload: dict = {
        "keyword": " ".join(keyword_set),
        "oppStatuses": "posted|forecasted",
        "rows": rows,
        "startRecord": start_record,
        "sortBy": "openDate|desc",
    }
    if agency_codes:
        payload["agencies"] = "|".join(agency_codes)

    try:
        resp = _post_json(GRANTS_GOV_SEARCH, payload)
    except Exception as e:
        logger.error("Search failed for keywords %s: %s", keyword_set, e)
        return []

    # Response shape varies by Grants.gov version; handle both
    opps = (
        resp.get("oppHits")
        or resp.get("opportunities")
        or resp.get("data", {}).get("hits", [])
        or []
    )
    logger.info("Search '%s' → %d results", " ".join(keyword_set), len(opps))
    if opps and logger.isEnabledFor(logging.DEBUG):
        logger.debug("First result keys: %s", list(opps[0].keys()))
    return opps


def fetch_detail(opp_number: str) -> Optional[dict]:
    """Fetch full opportunity detail from Grants.gov."""
    url = f"{GRANTS_GOV_DETAIL}{opp_number}"
    try:
        return _get_json(url)
    except Exception as e:
        logger.warning("Detail fetch failed for %s: %s", opp_number, e)
        return None


# ---------------------------------------------------------------------------
# Profile-driven ingest
# ---------------------------------------------------------------------------
def ingest_for_profile(profile: dict, fetch_details: bool = True) -> list[dict]:
    """
    Run all keyword searches from profile.search_seeds and return
    a deduplicated list of raw opportunity dicts.
    """
    seen_ids: set[str] = set()
    all_opps: list[dict] = []

    keyword_sets = profile.get("search_seeds", {}).get("keyword_sets", [])
    if not keyword_sets:
        logger.warning("Profile has no search_seeds.keyword_sets — returning empty")
        return []

    for kw_set in keyword_sets:
        opps = search_opportunities(kw_set)
        for opp in opps:
            # Numeric id for dedup; oppNumber (string) for detail fetch
            opp_id = str(opp.get("id") or opp.get("opportunityId") or opp.get("opportunity_id", ""))
            opp_number = str(opp.get("oppNumber") or opp.get("opportunityNumber") or opp.get("number") or opp_id)
            if not opp_id or opp_id in seen_ids:
                continue
            seen_ids.add(opp_id)
            if fetch_details:
                # Try oppNumber first (string form), fall back to numeric id
                detail = fetch_detail(opp_number)
                if not detail and opp_number != opp_id:
                    detail = fetch_detail(opp_id)
                if detail:
                    opp = {**opp, **detail}  # merge detail fields
            all_opps.append(opp)
        time.sleep(0.5)  # polite rate limiting

    logger.info("Ingest complete — %d unique opportunities", len(all_opps))
    return all_opps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    profile_path = sys.argv[1] if len(sys.argv) > 1 else "profiles/ncdot.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "data/sample/raw_ingest.json"

    with open(profile_path) as f:
        profile = json.load(f)

    opps = ingest_for_profile(profile, fetch_details=False)
    with open(out_path, "w") as f:
        json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "items": opps}, f, indent=2)
    print(f"Wrote {len(opps)} opportunities to {out_path}")
