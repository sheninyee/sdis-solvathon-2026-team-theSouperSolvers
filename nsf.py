"""
scrapers/nsf.py
---------------
Fetches NSF funding opportunities via RSS feed + HTML detail page enrichment.

Data flow:
    RSS feed (index) --> detail pages (enrichment) --> raw/{date}/nsf.json in Blob

Usage (called by job.py):
    from scrapers.nsf import ingest_nsf
    log_entry = ingest_nsf(blob_container_client)

Env vars:
    NSF_OFFLINE=true     Skip live fetch; load from scrapers/fixtures/nsf_sample.json
    NSF_MAX_DETAIL=30    Max detail pages to enrich (default 30)
    NSF_TIMEOUT=15       HTTP timeout in seconds (default 15)
    NSF_CRAWL_DELAY=1.0  Seconds to wait between detail page requests (default 1.0)
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NSF_RSS_URL  = "https://www.nsf.gov/rss/rss_www_funding.xml"
NSF_BASE_URL = "https://www.nsf.gov"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nsf_sample.json"

USER_AGENT = (
    "FFM-GrantBot/1.0 "
    "(Federal Funds Matchmaker research tool; "
    "contact your-email@example.com)"
)

# ---------------------------------------------------------------------------
# Public entry point — called by job.py
# ---------------------------------------------------------------------------

def ingest_nsf(container_client) -> dict:
    """
    Fetch NSF opportunities and write raw output to Blob Storage.

    Args:
        container_client: azure.storage.blob.ContainerClient for ffm-data.

    Returns:
        A run-log dict: {"source", "count", "blob_path", "errors"}.
    """
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blob_path = f"raw/{date_str}/nsf.json"
    errors: list[str] = []

    try:
        if os.getenv("NSF_OFFLINE", "").lower() == "true":
            logger.info("NSF_OFFLINE=true — loading fixture file")
            opportunities = _load_fixture()
        else:
            opportunities = fetch_nsf_opportunities(errors=errors)

        payload = {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "source":        "nsf.gov",
            "count":         len(opportunities),
            "errors":        errors,
            "opportunities": opportunities,
        }

        container_client.upload_blob(
            name=blob_path,
            data=json.dumps(payload, indent=2, default=str),
            overwrite=True,
        )
        logger.info("NSF ingest complete: %d opportunities -> %s", len(opportunities), blob_path)

    except Exception as exc:
        logger.exception("NSF ingest failed: %s", exc)
        errors.append(str(exc))
        return {"source": "nsf", "count": 0, "blob_path": blob_path, "errors": errors}

    return {"source": "nsf", "count": len(opportunities), "blob_path": blob_path, "errors": errors}


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

def fetch_nsf_opportunities(errors: Optional[list] = None) -> list[dict]:
    """
    Fetch and enrich NSF opportunities.

    1. Parse RSS feed to get the index of current opportunities.
    2. Enrich each entry with deadline, award ceiling, eligibility, and
       directorate scraped from the individual detail page.
    3. Return raw dicts ready for normalize_nsf_record().

    Args:
        errors: Optional list to collect non-fatal error messages.

    Returns:
        List of raw opportunity dicts.
    """
    if errors is None:
        errors = []

    max_detail = int(os.getenv("NSF_MAX_DETAIL", "30"))
    timeout    = int(os.getenv("NSF_TIMEOUT",     "15"))

    # -- Step 1: RSS index -------------------------------------------------
    logger.info("Fetching NSF RSS feed: %s", NSF_RSS_URL)
    feed = feedparser.parse(NSF_RSS_URL)

    if feed.bozo:
        # feedparser sets bozo=True on parse errors; log but continue
        logger.warning("RSS parse warning: %s", feed.bozo_exception)

    opportunities = []
    for entry in feed.entries:
        opp = {
            "source":        "nsf.gov",
            "title":         entry.get("title", "").strip(),
            "url":           entry.get("link", ""),
            "description":   _strip_html(entry.get("summary", "")),
            "published_at":  _parse_date(entry.get("published", "")[:20]),
            # Enriched below
            "close_date":    None,
            "award_ceiling": None,
            "directorate":   None,
            "program_id":    None,
            "eligibility":   [],
            "keywords":      [],
        }
        opportunities.append(opp)

    logger.info("RSS returned %d entries", len(opportunities))

    # -- Step 2: Enrich detail pages (top N only) --------------------------
    client = _build_http_client(timeout)
    for i, opp in enumerate(opportunities[:max_detail]):
        if not opp["url"]:
            continue
        logger.debug(
            "Enriching [%d/%d]: %s",
            i + 1, min(len(opportunities), max_detail), opp["url"]
        )
        try:
            enriched = _scrape_detail_page(opp["url"], client)
            opp.update(enriched)
        except httpx.TimeoutException:
            msg = f"Timeout fetching {opp['url']}"
            logger.warning(msg)
            errors.append(msg)
        except httpx.HTTPStatusError as exc:
            msg = f"HTTP {exc.response.status_code} for {opp['url']}"
            logger.warning(msg)
            errors.append(msg)
        except Exception as exc:
            msg = f"Parse error for {opp['url']}: {exc}"
            logger.warning(msg)
            errors.append(msg)

        # Be polite — don't hammer NSF's servers
        time.sleep(float(os.getenv("NSF_CRAWL_DELAY", "1.0")))

    client.close()
    return opportunities


# ---------------------------------------------------------------------------
# Detail page scraper
# ---------------------------------------------------------------------------

def _scrape_detail_page(url: str, client: httpx.Client) -> dict:
    """
    Scrape one NSF opportunity detail page.

    Returns a dict with any subset of:
        close_date, award_ceiling, directorate, program_id, eligibility, keywords
    """
    resp = client.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    result = {}

    # -- Deadline ----------------------------------------------------------
    deadline_labels = ["deadline", "due date", "target date", "submission date"]
    close_text = _extract_field_by_label(soup, deadline_labels)
    if close_text:
        result["close_date"] = _parse_date(close_text)

    # -- Award ceiling -----------------------------------------------------
    amount_labels = ["award ceiling", "maximum award", "funding amount", "award size"]
    amount_text = _extract_field_by_label(soup, amount_labels)
    if amount_text:
        result["award_ceiling"] = _parse_amount(amount_text)

    # -- Directorate / Division (NSF-specific) -----------------------------
    org_labels = ["directorate", "division", "program office"]
    org_text = _extract_field_by_label(soup, org_labels)
    if org_text:
        result["directorate"] = org_text[:200]

    # -- Program / solicitation number ------------------------------------
    id_labels = ["program number", "solicitation number", "nsf publication number"]
    id_text = _extract_field_by_label(soup, id_labels)
    if id_text:
        result["program_id"] = id_text.strip()[:50]

    # -- Eligibility -------------------------------------------------------
    result["eligibility"] = _extract_eligibility(soup)

    # -- Keywords from meta tags ------------------------------------------
    meta_kw = soup.find("meta", {"name": re.compile(r"keywords", re.I)})
    if meta_kw and meta_kw.get("content"):
        result["keywords"] = [
            k.strip() for k in meta_kw["content"].split(",") if k.strip()
        ]

    return result


def _extract_field_by_label(soup: BeautifulSoup, labels: list[str]) -> Optional[str]:
    """
    Find a value paired with a label on the page.

    Tries four common NSF markup patterns in order:
      1. <dt>label</dt><dd>value</dd>  (definition lists — most common)
      2. <th>label</th><td>value</td>  (tables)
      3. <strong>label</strong> + sibling/text node
      4. class="label" + class="value" siblings
    """
    pattern = re.compile("|".join(re.escape(l) for l in labels), re.I)

    # Pattern 1: definition list
    for dt in soup.find_all("dt"):
        if pattern.search(dt.get_text()):
            dd = dt.find_next_sibling("dd")
            if dd:
                return dd.get_text(strip=True)

    # Pattern 2: table row
    for th in soup.find_all("th"):
        if pattern.search(th.get_text()):
            td = th.find_next_sibling("td")
            if td:
                return td.get_text(strip=True)

    # Pattern 3: bold/strong label
    for tag in soup.find_all(["strong", "b", "label"]):
        if pattern.search(tag.get_text()):
            sibling = tag.find_next_sibling()
            if sibling:
                return sibling.get_text(strip=True)
            next_text = tag.next_sibling
            if next_text and str(next_text).strip():
                return str(next_text).strip()

    # Pattern 4: class-based label/value pairs
    for el in soup.find_all(class_=re.compile(r"label|field-label|key", re.I)):
        if pattern.search(el.get_text()):
            value_el = el.find_next_sibling(
                class_=re.compile(r"value|field-value|val", re.I)
            )
            if value_el:
                return value_el.get_text(strip=True)

    return None


def _extract_eligibility(soup: BeautifulSoup) -> list[str]:
    """
    Extract eligibility requirements from a detail page.
    NSF typically lists them in a <ul>/<ol> near the word 'eligib'.
    """
    eligibility = []
    pattern = re.compile(r"eligib", re.I)

    for heading in soup.find_all(string=pattern):
        parent = heading.find_parent()
        if not parent:
            continue
        for sibling in parent.find_next_siblings():
            if sibling.name in ("ul", "ol"):
                for li in sibling.find_all("li"):
                    text = li.get_text(strip=True)
                    if text:
                        eligibility.append(text[:300])
                break
            if sibling.name in ("h2", "h3", "h4"):
                break
        if eligibility:
            break

    # Fallback: id/class-based eligibility section
    if not eligibility:
        section = soup.find(id=re.compile(r"eligib", re.I)) or \
                  soup.find(class_=re.compile(r"eligib", re.I))
        if section:
            for li in section.find_all("li"):
                text = li.get_text(strip=True)
                if text:
                    eligibility.append(text[:300])

    return eligibility[:20]


# ---------------------------------------------------------------------------
# Normalization helper — called by pipeline/normalize.py
# ---------------------------------------------------------------------------

def normalize_nsf_record(raw: dict) -> dict:
    """
    Map a raw NSF scraped record to the project's minimum opportunity schema
    (defined in section 5.0 of the starter kit).

    Called by pipeline/normalize.py when processing raw/{date}/nsf.json.
    The scorer and app only ever see these normalized records, never raw dicts.
    """
    close_date    = raw.get("close_date")
    days_to_close = _days_to_close(close_date)

    return {
        # Core identity
        "id":            _make_id(raw),
        "source":        "nsf.gov",
        "number":        raw.get("program_id"),
        "title":         (raw.get("title") or "").strip(),

        # Agency / funder
        "agency":        "National Science Foundation",
        "sub_agency":    raw.get("directorate"),

        # Status — NSF does not use posted/forecasted; default to "posted"
        "status":        "posted",

        # Dates
        "open_date":     (raw.get("published_at") or "")[:10] or None,
        "close_date":    close_date,
        "days_to_close": days_to_close,

        # Eligibility and categorization
        "eligibility":   raw.get("eligibility", []),
        "category":      raw.get("directorate"),

        # Funding
        "award_ceiling": raw.get("award_ceiling"),

        # Content
        "description":   (raw.get("description") or "").strip()[:2000],
        "keywords":      raw.get("keywords", []),
        "url":           raw.get("url", ""),

        # Provenance
        "ingested_at":   datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _make_id(raw: dict) -> str:
    """Generate a stable ID from the URL path or title."""
    url = raw.get("url", "")
    # Extract NSF program number from URL, e.g. /pubs/2024/nsf24001/
    match = re.search(r"/nsf(\d+)/", url)
    if match:
        return f"nsf-{match.group(1)}"
    title = raw.get("title", "untitled")
    slug  = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    return f"nsf-{slug}"


def _build_http_client(timeout: int) -> httpx.Client:
    """Build a shared httpx client with appropriate headers and timeout."""
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept":     "text/html,application/xhtml+xml",
        },
    )


def _strip_html(text: str) -> str:
    """Remove HTML tags from RSS summary text."""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)


def _parse_date(text: str) -> Optional[str]:
    """
    Parse various date formats found on NSF pages to YYYY-MM-DD.
    Returns None if no recognized format matches.
    """
    if not text:
        return None
    text = text.strip()
    formats = (
        "%B %d, %Y",   # January 15, 2025
        "%b %d, %Y",   # Jan 15, 2025
        "%m/%d/%Y",    # 01/15/2025
        "%Y-%m-%d",    # 2025-01-15
        "%d-%b-%Y",    # 15-Jan-2025
    )
    for fmt in formats:
        try:
            return datetime.strptime(text[:20], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_amount(text: str) -> Optional[int]:
    """
    Extract the largest dollar amount from a string.
    Handles '$2,000,000', '2M', '$500K', '1.5 million'.
    """
    if not text:
        return None
    normalized = text.lower().replace(",", "")

    m = re.search(r"\$?([\d.]+)\s*million", normalized)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"\$?([\d.]+)\s*m\b", normalized)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"\$?([\d.]+)\s*k\b", normalized)
    if m:
        return int(float(m.group(1)) * 1_000)
    m = re.search(r"\$?([\d]+)", normalized)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _days_to_close(close_date: Optional[str]) -> Optional[int]:
    """Return days until close_date from today (UTC). Returns None if unknown."""
    if not close_date:
        return None
    try:
        close_dt = datetime.strptime(close_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max((close_dt - datetime.now(timezone.utc)).days, 0)
    except ValueError:
        return None


def _load_fixture() -> list[dict]:
    """Load the offline fixture file (used when NSF_OFFLINE=true)."""
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(
            f"Fixture not found at {FIXTURE_PATH}. "
            "Run once with NSF_OFFLINE=false, then save the output as "
            "scrapers/fixtures/nsf_sample.json."
        )
    with FIXTURE_PATH.open() as f:
        data = json.load(f)
    return data.get("opportunities", data) if isinstance(data, dict) else data


# ---------------------------------------------------------------------------
# CLI helper — run directly to test the scraper locally
#   python -m scrapers.nsf
#   python -m scrapers.nsf --save-fixture
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    errors: list[str] = []
    opps = fetch_nsf_opportunities(errors=errors)

    print(f"\nFetched {len(opps)} opportunities")
    if errors:
        print(f"Non-fatal errors ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

    for opp in opps[:3]:
        print("\n---")
        print(f"Title:        {opp['title']}")
        print(f"URL:          {opp['url']}")
        print(f"Close date:   {opp['close_date']}")
        print(f"Award ceil.:  {opp['award_ceiling']}")
        print(f"Directorate:  {opp['directorate']}")
        print(f"Eligibility:  {opp['eligibility'][:2]}")

    if "--save-fixture" in sys.argv:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FIXTURE_PATH.open("w") as f:
            json.dump({"opportunities": opps}, f, indent=2, default=str)
        print(f"\nFixture saved -> {FIXTURE_PATH}")
