"""data.py - loads the grants CSV and aggregates it for the dashboard."""
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse your scoring functions (see note: wrap the bottom of score.py in
# `if __name__ == "__main__":` so importing it doesn't re-run the script).
from score import average_from_award_range, expected_number_of_awards

CSV_PATH = Path(__file__).with_name("grants-search-202608182008.csv")
SCORE_UNIT = 1_000_000          # score.py's curve expects small numbers, so dollars -> millions
SIZE_LABELS = ["Under $250K", "$250K\u2013$1M", "Over $1M"]
TOP_CATEGORIES = 6              # 6 named categories + "Other" (matches the 7 chart colors)


def _first_category(value):
    if not isinstance(value, str) or not value.strip():
        return "Other"
    value = re.sub(r"[\[\]{}'\"]", "", value)
    first = re.split(r"[;|,]", value)[0].strip().replace("_", " ").title()
    return first or "Other"


def load():
    df = pd.read_csv(CSV_PATH)
    df["agency"] = df["top_level_agency_name"].fillna("(Unknown)").astype(str).str.strip()

    raw = df["funding_categories"].map(_first_category)
    top = raw[raw != "Other"].value_counts().head(TOP_CATEGORIES).index
    df["funding_category"] = raw.where(raw.isin(top), "Other")

    df["year"] = pd.to_datetime(df["post_date"], errors="coerce").dt.year
    df["opp_type"] = df["category"].fillna("other").astype(str).str.title()
    df["funding"] = df["estimated_total_program_funding"]

    ceiling, floor = df["award_ceiling"], df["award_floor"]
    df["size_band"] = pd.cut(ceiling, [-np.inf, 250_000, 1_000_000, np.inf], labels=SIZE_LABELS)

    # Scores from score.py (average of ceiling/floor when both exist, else whichever exists)
    avg_award = pd.concat([ceiling, floor], axis=1).mean(axis=1)
    df["award_score"] = avg_award.map(
        lambda v: average_from_award_range(v / SCORE_UNIT) if pd.notna(v) else np.nan)
    df["expected_score"] = df["expected_number_of_awards"].map(
        lambda v: expected_number_of_awards(v) if pd.notna(v) else np.nan)
    return df


DF = load()
CATEGORIES = sorted(c for c in DF["funding_category"].unique() if c != "Other") + ["Other"]


def meta():
    return {
        "categories": CATEGORIES,
        "agencies": sorted(DF["agency"].unique()),
        "years": [int(y) for y in sorted(DF["year"].dropna().unique(), reverse=True)],
        "types": sorted(DF["opp_type"].unique()),
        "size_labels": SIZE_LABELS,
        "total": int(len(DF)),
    }


def apply_filters(df, year=None, opp_type=None, agencies=None):
    if year not in (None, "", "all"):
        df = df[df["year"] == int(year)]
    if opp_type not in (None, "", "all"):
        df = df[df["opp_type"] == opp_type]
    if agencies:
        df = df[df["agency"].isin(agencies)]
    return df


def summarize(df):
    """One row per agency x funding category (the JS builds every chart from this)."""
    g = df.groupby(["agency", "funding_category"], observed=True)
    out = g.agg(
        n=("opportunity_id", "size"),
        funding=("funding", "sum"),
        ceil_n=("size_band", "count"),
        small=("size_band", lambda s: int((s == SIZE_LABELS[0]).sum())),
        mid=("size_band", lambda s: int((s == SIZE_LABELS[1]).sum())),
        large=("size_band", lambda s: int((s == SIZE_LABELS[2]).sum())),
        award_sum=("award_score", "sum"), award_n=("award_score", "count"),
        exp_sum=("expected_score", "sum"), exp_n=("expected_score", "count"),
    ).reset_index()
    return out


def payload(year=None, opp_type=None, agencies=None):
    rows = summarize(apply_filters(DF, year, opp_type, agencies))
    # Year chart ignores the year filter (like the Shiny app) so you see every year
    by_year = apply_filters(DF, None, opp_type, agencies).groupby("year").size()
    return {
        "rows": rows.to_dict("records"),
        "years": [{"year": int(y), "n": int(n)} for y, n in by_year.items()],
    }


def download_csv(year=None, opp_type=None, agencies=None):
    return summarize(apply_filters(DF, year, opp_type, agencies)).to_csv(index=False)