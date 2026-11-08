"""score.py — 5-signal scoring engine for Federal Funds Matchmaker.

Signals and weights (must sum to 100):
  semantic               43  — cosine similarity of embeddings (disabled w/o Azure OpenAI)
  keyword                24  — TF-IDF overlap against NCDOT profile keywords
  eligibility            14  — rule-based: state DOT eligible? match quality?
  timing                  9  — recency and urgency of deadline
  funder_affinity         5  — known DOT/FHWA/FTA funder bonus
  opportunity_attract     5  — award size (linear) + awardee count (binned), both capped at 100

Output per opportunity: score 0-100, confidence (High/Medium/Low), matched_units[],
score_breakdown{}, effort{level, hours_est, notes}.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
W_SEMANTIC    = 0.43
W_KEYWORD     = 0.24
W_ELIGIBILITY = 0.14
W_TIMING      = 0.09
W_FUNDER      = 0.05
W_ATTRACT     = 0.05  # opportunity attractiveness: award size + awardee count

# ---------------------------------------------------------------------------
# Funder affinity table — bonus when funder string matches
# ---------------------------------------------------------------------------
FUNDER_AFFINITY: dict[str, float] = {
    "fhwa": 1.0,
    "federal highway": 1.0,
    "fta": 1.0,
    "federal transit": 1.0,
    "fra": 0.9,
    "federal railroad": 0.9,
    "usdot": 0.8,
    "department of transportation": 0.8,
    "dot": 0.8,
    "faa": 0.7,
    "federal aviation": 0.7,
}

# ---------------------------------------------------------------------------
# Effort heuristics — rule-based text signals (D1: always labeled "estimate")
# ---------------------------------------------------------------------------
_CONSORTIUM_RE = re.compile(
    r"\b(partnership|consortium|multi-?agency|letters?\s+of\s+support|collaborative)\b", re.I
)
_NEPA_RE = re.compile(
    r"\b(NEPA|environmental\s+review|benefit-?cost\s+analysis|BCA)\b", re.I
)
EFFORT_HOURS = {"Low": "20–40", "Medium": "40–80", "High": "80+"}


def _compute_effort(opp: dict) -> dict:
    """Rule-based effort estimate. Points: cost_share +1, large/multi-year +1,
    consortium language +1, NEPA/env review +1. 0-1→Low, 2→Medium, 3+→High."""
    text = " ".join(filter(None, [
        opp.get("synopsis", ""),
        opp.get("description", ""),
        opp.get("source_text", ""),
        opp.get("eligibility_note", ""),
    ]))

    if not text.strip():
        return {"level": "not stated", "hours_range": None, "basis": "Insufficient text to estimate."}

    pts = 0
    triggers = []

    # Cost share
    if opp.get("cost_share") or opp.get("cost_sharing"):
        pts += 1
        triggers.append("cost share required")

    # Large award or multi-year scope
    max_award = opp.get("max_award_usd") or 0
    if max_award >= 10_000_000:
        pts += 1
        triggers.append(f"large award (${max_award:,.0f})")
    elif re.search(r"\bmulti-?year\b|\b(5|four|five|six|seven)\s+year", text, re.I):
        pts += 1
        triggers.append("multi-year scope")

    # Consortium/partnership language
    if _CONSORTIUM_RE.search(text):
        pts += 1
        triggers.append("consortium/partnership language")

    # Environmental/NEPA review
    if _NEPA_RE.search(text):
        pts += 1
        triggers.append("NEPA or environmental review")

    if pts <= 1:
        level = "Low"
    elif pts == 2:
        level = "Medium"
    else:
        level = "High"

    hours = EFFORT_HOURS[level]
    basis = "; ".join(triggers) if triggers else "no complexity signals found"
    return {"level": level, "hours_range": hours, "basis": basis.capitalize() + "."}


def _days_to_close(close_date_str: Optional[str], status: str = "posted") -> Optional[int]:
    """Days until close date. Returns None for forecasted or missing dates."""
    if not close_date_str or (status or "").lower() == "forecasted":
        return None
    try:
        close = date.fromisoformat(close_date_str[:10])
        return max(0, (close - date.today()).days)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Profile loader
# ---------------------------------------------------------------------------
def load_profile(profile_path: str | Path) -> dict:
    return json.loads(Path(profile_path).read_text())


def _all_keywords(profile: dict) -> list[str]:
    """Flatten all keywords from every matching unit."""
    kws: list[str] = []
    for unit in profile.get("matching_units", []):
        kws.extend(unit.get("keywords_plan", []))
        kws.extend(unit.get("keywords_expansion", []))
    return [k.lower() for k in kws]


def _unit_keyword_map(profile: dict) -> dict[str, list[str]]:
    """Return {unit_id: [keywords]} for overlap detection."""
    out: dict[str, list[str]] = {}
    for unit in profile.get("matching_units", []):
        kws = [k.lower() for k in unit.get("keywords_plan", []) + unit.get("keywords_expansion", [])]
        out[unit["id"]] = kws
    return out


# ---------------------------------------------------------------------------
# Signal 1 — Semantic (embedding cosine similarity)
# Returns 0.0–1.0; falls back to 0.0 if embeddings unavailable.
# ---------------------------------------------------------------------------
def semantic_score(opp_text: str, profile_embedding: Optional[list[float]]) -> float:
    if profile_embedding is None:
        return 0.0
    try:
        import numpy as np
        from openai import AzureOpenAI

        endpoint = os.environ.get("AOAI_ENDPOINT", "")
        key = os.environ.get("AOAI_KEY", "")
        emb_model = os.environ.get("AOAI_EMBEDDING_MODEL", "text-embedding-3-small")
        if not endpoint or not key:
            return 0.0

        client = AzureOpenAI(azure_endpoint=endpoint, api_key=key, api_version="2024-02-01")
        resp = client.embeddings.create(input=[opp_text], model=emb_model)
        vec = np.array(resp.data[0].embedding)
        ref = np.array(profile_embedding)
        sim = float(np.dot(vec, ref) / (np.linalg.norm(vec) * np.linalg.norm(ref) + 1e-9))
        return max(0.0, min(1.0, (sim + 1) / 2))  # rescale -1..1 → 0..1
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Signal 2 — Keyword / TF-IDF overlap
# Returns 0.0–1.0
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "the", "a", "an", "and", "or", "in", "of", "to", "for", "with",
    "is", "are", "that", "this", "on", "at", "by", "be", "from",
    "as", "it", "its", "will", "has", "have", "been", "not", "all",
}


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+(?:[/-][a-z0-9]+)*", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def keyword_score(opp_text: str, all_keywords: list[str]) -> float:
    if not opp_text or not all_keywords:
        return 0.0
    tokens = set(_tokenize(opp_text))
    kw_set = set(all_keywords)

    # Exact hits
    exact_hits = tokens & kw_set
    # Partial hits (opp token is substring of a keyword or vice versa)
    partial_hits = set()
    for t in tokens:
        for kw in kw_set:
            if t != kw and (t in kw or kw in t) and len(t) > 3:
                partial_hits.add(t)
                break

    exact_score = len(exact_hits) / max(1, len(kw_set))
    partial_score = len(partial_hits) / max(1, len(kw_set)) * 0.4

    raw = min(1.0, exact_score * 2 + partial_score)  # boost exact, cap at 1
    # Sigmoid smoothing so 20% keyword coverage → ~0.6 score
    return 1 / (1 + math.exp(-6 * (raw - 0.15)))


def matched_units_for(opp_text: str, unit_kw_map: dict[str, list[str]]) -> list[str]:
    """Return list of matching unit IDs that have ≥1 keyword hit."""
    tokens = set(_tokenize(opp_text))
    matched = []
    for unit_id, kws in unit_kw_map.items():
        if tokens & set(kws):
            matched.append(unit_id)
    return matched


# ---------------------------------------------------------------------------
# Signal 3 — Eligibility
# Returns 0.0–1.0
# ---------------------------------------------------------------------------
_ELIGIBLE_PATTERNS = [
    r"\bstate\s+dot\b", r"\bstate\s+department\s+of\s+transportation\b",
    r"\bstate\s+transportation\s+agenc", r"\bstate\s+government\b",
    r"\bstate\s+and\s+local\b", r"\bgovernment\s+entit",
    r"\bstate\s+agenc", r"\beligible\s+applicants.*state\b",
]
_INELIGIBLE_PATTERNS = [
    r"\bnonprofit\s+only\b", r"\bonly\s+nonprofit",
    r"\bsmall\s+business\s+only\b", r"\buniversity\s+only\b",
    r"\bindividual\s+applicants\s+only\b",
]


def eligibility_score(opp_text: str, eligibility_note: str = "") -> float:
    text = (opp_text + " " + eligibility_note).lower()
    for pat in _INELIGIBLE_PATTERNS:
        if re.search(pat, text):
            return 0.1  # very unlikely eligible
    hits = sum(1 for pat in _ELIGIBLE_PATTERNS if re.search(pat, text))
    if hits >= 2:
        return 1.0
    if hits == 1:
        return 0.75
    # No clear signal — give benefit of doubt for DOT-sourced opps
    if any(w in text for w in ["state", "dot", "transportation department"]):
        return 0.6
    return 0.4


# ---------------------------------------------------------------------------
# Signal 4 — Timing
# Returns 0.0–1.0
# ---------------------------------------------------------------------------
def timing_score(close_date_str: Optional[str], posted_date_str: Optional[str], status: str) -> float:
    today = date.today()
    if status == "forecasted":
        return 0.4  # future opportunity, moderate score

    if not close_date_str:
        return 0.5  # formula program or no deadline stated

    try:
        close = date.fromisoformat(close_date_str[:10])
    except (ValueError, TypeError):
        return 0.5

    days_left = (close - today).days
    if days_left < 0:
        return 0.0  # closed

    # Sweet spot: 14–90 days — high score, allows prep time
    if days_left <= 7:
        return 0.3   # too tight to apply well
    if days_left <= 14:
        return 0.6
    if days_left <= 90:
        return 1.0
    if days_left <= 180:
        return 0.8
    return 0.5  # very far out


# ---------------------------------------------------------------------------
# Signal 5 — Funder affinity
# Returns 0.0–1.0
# ---------------------------------------------------------------------------
def funder_affinity_score(funder: str) -> float:
    fl = funder.lower()
    for key, val in FUNDER_AFFINITY.items():
        if key in fl:
            return val
    return 0.3  # unknown funder, low but nonzero

# ---------------------------------------------------------------------------
# Signal 6 — Opportunity attractiveness (award size + awardee count)
# Award score:   0.00007 × avg_award_usd, capped at 100
# Awardee score: binned 0/20/40/60/80/100 by number of expected awards
# Combined:      sum of both, capped at 100, then normalised to 0–1.
# ---------------------------------------------------------------------------
_AWARDEE_BINS: list[tuple[int, int]] = [
    (1,   0),
    (10,  20),
    (30,  40),
    (50,  60),
    (100, 80),
]


def opportunity_attractiveness_score(
    avg_award_usd: Optional[float],
    num_awards: Optional[int],
) -> float:
    """Return 0.0–1.0 reflecting award size and number of awardees."""
    # Award-amount sub-score (linear, capped)
    award_pts = 0.0
    if avg_award_usd and avg_award_usd > 0:
        award_pts = min(100.0, 0.00007 * avg_award_usd)

    # Awardee-count sub-score (binned)
    awardee_pts = 0.0
    if num_awards is not None and num_awards > 0:
        if num_awards > 100:
            awardee_pts = 100.0
        else:
            for threshold, pts in reversed(_AWARDEE_BINS):
                if num_awards > threshold:
                    awardee_pts = pts
                    break

    return min(100.0, award_pts + awardee_pts) / 100.0


# ---------------------------------------------------------------------------
# Confidence label
# ---------------------------------------------------------------------------
def confidence_label(score: float, mode: str, eligibility: float) -> str:
    if mode == "keyword_fallback":
        # Without embeddings, downgrade confidence
        if score >= 80 and eligibility >= 0.75:
            return "High"
        if score >= 55 and eligibility >= 0.5:
            return "Medium"
        return "Low"
    # With embeddings
    if score >= 75:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------
def score_opportunity(
    opp: dict,
    profile: dict,
    profile_embedding: Optional[list[float]] = None,
    mode: str = "keyword_fallback",
) -> dict:
    """
    Score one opportunity dict against a profile.

    Returns the opp dict enriched with:
      score, confidence, matched_units, score_breakdown, effort
    """
    all_kws = _all_keywords(profile)
    unit_kw_map = _unit_keyword_map(profile)

    # Build text blob for scoring
    title = opp.get("title", "")
    description = opp.get("description", opp.get("synopsis", ""))
    eligibility_text = opp.get("eligibility_note", opp.get("eligibility_types", ""))
    source_text = opp.get("source_text", "")
    opp_text = " ".join([title, description, eligibility_text, source_text])

    # Compute signals
    sem = semantic_score(opp_text, profile_embedding)
    kw = keyword_score(opp_text, all_kws)
    elig = eligibility_score(opp_text, eligibility_text)

    # Account-aware eligibility verdict (displayed in UI)
    applicant_types = opp.get("applicant_types", [])  # from detail fetch
    account_types = profile.get("applicant_types", [])  # from account profile
    elig_note = opp.get("eligibility_note", "")

    if applicant_types and account_types:
        # Check overlap between account types and listing types
        acct_set = {t.lower().strip() for t in account_types}
        listing_set = {t.lower().strip() for t in applicant_types}
        # Also try partial match (e.g. "state governments" matches "State government")
        overlap = any(
            any(a in l or l in a for l in listing_set)
            for a in acct_set
        )
        if overlap:
            elig_verdict = "Eligible"
            elig_reason = f"Account type matches: {', '.join(applicant_types[:3])}"
        else:
            # Check if unrestricted
            if any(w in elig_note.lower() for w in ["all organizations", "unrestricted", "any applicant", "all entities"]):
                elig_verdict = "Eligible"
                elig_reason = "Unrestricted — open to all applicants"
            else:
                elig_verdict = "Not eligible"
                elig_reason = f"Listed types ({', '.join(applicant_types[:3])}) do not match account type"
    elif elig_note:
        # Check for exclusionary language
        exclusion_re = re.compile(r"\b(only|must be|limited to|tribal|nonprofit|501\(c\)|for-profit|small business|individual\s+applicants?)\b", re.I)
        m = exclusion_re.search(elig_note)
        if m:
            # Find the sentence containing the match
            sentences = [s.strip() for s in re.split(r"[.!?]", elig_note) if m.group() in s]
            reason = sentences[0] if sentences else elig_note[:120]
            elig_verdict = "Unclear"
            elig_reason = f"Check listing: '{reason[:100]}'"
        else:
            elig_verdict = "Unclear"
            elig_reason = "No applicant type data — verify eligibility in listing"
    else:
        elig_verdict = "Unclear"
        elig_reason = "No eligibility information found"
    timing = timing_score(
        opp.get("close_date"),
        opp.get("posted_date"),
        opp.get("status", "posted"),
    )
    funder = funder_affinity_score(opp.get("funder", ""))
    attract = opportunity_attractiveness_score(
        opp.get("avg_award_usd") or opp.get("award_ceiling"),
        opp.get("expected_number_of_awards") or opp.get("num_awards"),
    )

    # In keyword_fallback mode, redistribute semantic weight to keyword
    if mode == "keyword_fallback" or sem == 0.0:
        w_sem, w_kw = 0.0, W_KEYWORD + W_SEMANTIC
    else:
        w_sem, w_kw = W_SEMANTIC, W_KEYWORD

    raw = (
        sem * w_sem
        + kw * w_kw
        + elig * W_ELIGIBILITY
        + timing * W_TIMING
        + funder * W_FUNDER
        + attract * W_ATTRACT
    )

    score = round(min(100, max(0, raw * 100)))
    conf = confidence_label(score, mode, elig)
    matched = matched_units_for(opp_text, unit_kw_map)

    # Effort from max_award if available
    effort = opp.get("effort") or _compute_effort(opp)

    return {
        **opp,
        "score": score,
        "confidence": conf,
        "matched_units": matched,
        "score_breakdown": {
            "semantic": round(sem * w_sem * 100),
            "keyword": round(kw * w_kw * 100),
            "eligibility": round(elig * W_ELIGIBILITY * 100),
            "timing": round(timing * W_TIMING * 100),
            "funder_affinity": round(funder * W_FUNDER * 100),
            "opportunity_attractiveness": round(attract * W_ATTRACT * 100),
        },
        "effort": effort,
        "eligibility_verdict": elig_verdict,
        "eligibility_reason": elig_reason,
        "applicant_types": applicant_types,
        "days_to_close": _days_to_close(opp.get("close_date"), opp.get("status", "posted")),
    }


def score_all(
    opportunities: list[dict],
    profile: dict,
    profile_embedding: Optional[list[float]] = None,
    mode: str = "keyword_fallback",
) -> list[dict]:
    """Score and rank a list of opportunities. Returns sorted by score desc."""
    scored = [score_opportunity(o, profile, profile_embedding, mode) for o in opportunities]
    scored.sort(key=lambda x: x["score"], reverse=True)
    for i, item in enumerate(scored, start=1):
        item["rank"] = i
    return scored


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    profile_path = sys.argv[1] if len(sys.argv) > 1 else "profiles/ncdot.json"
    sample_path = sys.argv[2] if len(sys.argv) > 2 else "data/sample/scored_ncdot_latest.json"

    profile = load_profile(profile_path)
    with open(sample_path) as f:
        data = json.load(f)

    items = data.get("items", [])
    rescored = score_all(items, profile, mode="keyword_fallback")
    for item in rescored:
        print(f"  {item['rank']:2d}. [{item['score']:3d}] {item['confidence']:6s}  {item['title'][:70]}")
