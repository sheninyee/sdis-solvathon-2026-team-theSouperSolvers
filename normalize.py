"""normalize.py — Clean and normalize raw Grants.gov opportunity dicts.

Responsibilities:
  - Map varying field names from Grants.gov to our canonical schema
  - Parse and validate dates (ISO 8601 output)
  - Deduplicate by opportunity number
  - Add quality flags (missing_description, past_deadline, low_quality)
  - Enrich funder name from agency code
  - Strip HTML from synopsis text
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agency code → display name
# ---------------------------------------------------------------------------
AGENCY_DISPLAY: dict[str, str] = {
    "USDOT": "US Department of Transportation",
    "DOT": "US Department of Transportation",
    "FHWA": "Federal Highway Administration (FHWA)",
    "FTA": "Federal Transit Administration (FTA)",
    "FRA": "Federal Railroad Administration (FRA)",
    "FAA": "Federal Aviation Administration (FAA)",
    "FMCSA": "Federal Motor Carrier Safety Administration",
    "NHTSA": "National Highway Traffic Safety Administration (NHTSA)",
    "EPA": "US Environmental Protection Agency",
    "HUD": "US Dept. of Housing and Urban Development",
    "EDA": "Economic Development Administration (EDA)",
    "USDA": "US Dept. of Agriculture (USDA)",
}


def _agency_display(code: str, raw_name: str) -> str:
    return AGENCY_DISPLAY.get(code.upper(), raw_name or code)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%Y%m%d",
]


def _parse_date(val: Optional[str]) -> Optional[str]:
    """Return ISO date string YYYY-MM-DD or None."""
    if not val:
        return None
    val = str(val).strip()
    if val.lower() in ("null", "none", "", "n/a"):
        return None
    # Try known formats
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(val[:19], fmt).date().isoformat()
        except ValueError:
            continue
    # Try epoch millis
    try:
        ts = int(val)
        if ts > 1e10:  # millis
            ts //= 1000
        return date.fromtimestamp(ts).isoformat()
    except (ValueError, TypeError, OSError):
        pass
    logger.debug("Could not parse date: %r", val)
    return None


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s{2,}", " ", text).strip()


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------
def _get_first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "" and v != "null":
            return v
    return default


def _status(raw: str) -> str:
    s = str(raw).lower()
    if "forecast" in s:
        return "forecasted"
    if "post" in s or "open" in s or "active" in s:
        return "posted"
    if "close" in s or "archiv" in s:
        return "closed"
    return "unknown"


# ---------------------------------------------------------------------------
# Main normalize function
# ---------------------------------------------------------------------------
def normalize_opportunity(raw: dict) -> Optional[dict]:
    """
    Map a raw Grants.gov opportunity dict to canonical schema.
    Returns None if the opportunity should be dropped (e.g., closed, duplicate key missing).
    """
    opp_id = str(
        _get_first(raw, "id", "oppNumber", "opportunity_id", "opportunityId", default="")
    ).strip()
    if not opp_id:
        logger.debug("Dropping opportunity with no ID: %r", raw.get("title", ""))
        return None

    title = _strip_html(
        _get_first(raw, "title", "opportunityTitle", "oppTitle", default="Untitled")
    )
    agency_code = str(
        _get_first(raw, "agencyCode", "agency_code", "agcCode", default="")
    ).upper()
    agency_name = str(
        _get_first(raw, "agencyName", "agency_name", "agencyDesc", default=agency_code)
    )
    funder = _agency_display(agency_code, agency_name)

    close_date = _parse_date(
        _get_first(raw, "closeDate", "close_date", "closingDate", "archiveDate")
    )
    posted_date = _parse_date(
        _get_first(raw, "openDate", "posted_date", "postDate", "openingDate")
    )

    status_raw = str(
        _get_first(raw, "oppStatus", "status", "opportunityStatus", default="posted")
    )
    status = _status(status_raw)

    # Drop already-closed opportunities
    if status == "closed":
        return None
    today = date.today().isoformat()
    if close_date and close_date < today:
        return None

    cfda = str(_get_first(raw, "cfdaNumber", "cfda_number", "cfdaList", default=""))
    synopsis = _strip_html(
        _get_first(raw, "synopsis", "description", "synopsisDesc", "synopsisText", default="")
    )
    eligibility_note = _strip_html(
        _get_first(raw, "eligibilities", "eligibilityText", "applicantTypes", default="")
    )

    # Applicant types as a list (from detail fetch synopsis.applicantTypes[])
    raw_appl_types = (
        raw.get("synopsis", {}).get("applicantTypes")
        or raw.get("applicantTypes")
        or raw.get("applicant_types")
        or []
    )
    if isinstance(raw_appl_types, list):
        applicant_types = [
            (t.get("applicantTypeDesc") or t.get("description") or str(t)).strip()
            for t in raw_appl_types
            if t
        ]
    else:
        applicant_types = []

    # Cost share (bool)
    cost_share_raw = (
        raw.get("synopsis", {}).get("costSharing")
        or raw.get("costSharing")
        or raw.get("cost_sharing")
        or raw.get("cost_share")
    )
    cost_share = bool(cost_share_raw) if cost_share_raw is not None else None

    # Description (synopsisDesc from detail — may contain HTML, strip it)
    description = _strip_html(
        raw.get("synopsis", {}).get("synopsisDesc")
        or raw.get("synopsisDesc")
        or raw.get("description")
        or ""
    )

    # Contact info from detail
    contact_name = (
        raw.get("synopsis", {}).get("agencyContactName")
        or raw.get("agencyContactName")
        or raw.get("contactName")
        or ""
    )
    contact_email = (
        raw.get("synopsis", {}).get("agencyContactEmail")
        or raw.get("agencyContactEmail")
        or raw.get("contactEmail")
        or ""
    )

    # Funding totals
    num_awards = (
        raw.get("synopsis", {}).get("expectedNumberOfAwards")
        or raw.get("expectedNumberOfAwards")
        or raw.get("num_awards")
    )
    estimated_funding = None
    try:
        ef_raw = (
            raw.get("synopsis", {}).get("estimatedFunding")
            or raw.get("estimatedFunding")
            or raw.get("estimated_funding")
        )
        if ef_raw:
            estimated_funding = float(str(ef_raw).replace(",", ""))
    except (ValueError, TypeError):
        pass

    try:
        award_ceiling = float(
            str(_get_first(raw, "awardCeiling", "award_ceiling", "maxAward",
                           raw.get("synopsis", {}).get("awardCeiling"), default=0) or 0)
            .replace(",", "")
        )
    except (ValueError, TypeError):
        award_ceiling = None

    try:
        award_floor = float(
            str(_get_first(raw, "awardFloor", "award_floor", "minAward",
                           raw.get("synopsis", {}).get("awardFloor"), default=0) or 0)
            .replace(",", "")
        )
    except (ValueError, TypeError):
        award_floor = None

    url_base = "https://www.grants.gov/search-results-detail/"
    url = _get_first(raw, "url", "opportunityUrl", default=f"{url_base}{opp_id}")

    # Quality flags
    flags: list[str] = []
    if not synopsis:
        flags.append("missing_description")
    if award_ceiling is None:
        flags.append("missing_award_ceiling")

    return {
        "id": opp_id,
        "title": title,
        "funder": funder,
        "agency_code": agency_code,
        "cfda": cfda,
        "url": url,
        "status": status,
        "close_date": close_date,
        "posted_date": posted_date,
        "max_award_usd": award_ceiling,
        "min_award_usd": award_floor,
        "synopsis": synopsis,
        "description": description or synopsis,
        "eligibility_note": eligibility_note,
        "applicant_types": applicant_types,
        "cost_share": cost_share,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "num_awards": num_awards,
        "estimated_funding": estimated_funding,
        "source_text": synopsis[:1000] if synopsis else "",
        "quality_flags": flags,
    }


# ---------------------------------------------------------------------------
# Batch normalize
# ---------------------------------------------------------------------------
def normalize_all(raw_opps: list[dict]) -> list[dict]:
    """Normalize and deduplicate a list of raw opportunities."""
    seen: set[str] = set()
    out: list[dict] = []
    for raw in raw_opps:
        try:
            norm = normalize_opportunity(raw)
        except Exception as e:
            logger.warning("Normalize error: %s — skipping", e)
            continue
        if norm is None:
            continue
        if norm["id"] in seen:
            continue
        seen.add(norm["id"])
        out.append(norm)
    logger.info("Normalized %d → %d unique active opportunities", len(raw_opps), len(out))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    in_path = sys.argv[1] if len(sys.argv) > 1 else "data/sample/raw_ingest.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "data/sample/normalized.json"

    with open(in_path) as f:
        data = json.load(f)

    raw = data.get("items", data) if isinstance(data, dict) else data
    normed = normalize_all(raw)

    with open(out_path, "w") as f:
        json.dump({
            "normalized_at": datetime.now(timezone.utc).isoformat(),
            "count": len(normed),
            "items": normed,
        }, f, indent=2)
    print(f"Normalized {len(normed)} opportunities → {out_path}")
