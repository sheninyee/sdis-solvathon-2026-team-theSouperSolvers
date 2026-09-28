"""
filter_grants.py

Filters a grants.gov-style opportunity export (like grants-search-*.csv) down
to only the opportunities a given applicant is actually eligible for, based
on the "applicant_types" column.

HOW TO USE
----------
1. Set INPUT_CSV / OUTPUT_CSV below (or pass them as command-line args).
2. Describe who you are in APPLICANT_PROFILE (or answer the interactive
   prompts by running with --interactive).
3. Run:  python3 data_filtering.py
         python3 data_filtering.py --interactive
         python3 data_filtering.py input.csv output.csv --interactive

The script keeps a row if ANY of the applicant types you identify with
appear in that opportunity's "applicant_types" list, or if the opportunity
is marked "unrestricted". Everything else (opportunities that only list
applicant types you are NOT) gets excluded.
"""

import argparse
import sys
import pandas as pd

# --------------------------------------------------------------------------
# 1. ALL APPLICANT TYPE CODES USED IN THE DATASET
#    (key -> human-readable label). This is just for reference / prompts.
# --------------------------------------------------------------------------
ALL_APPLICANT_TYPES = {
    "governance_level":
        {
        "state_governments": "State governments",
        "county_governments": "County governments",
        "city_or_township_governments": "City or township governments",
        "special_district_governments": "Special district governments",
        },
    "tribal_status":
        {
        "federally_recognized_native_american_tribal_governments":
            "Federally recognized Native American tribal governments",
        "other_native_american_tribal_organizations":
            "Other Native American tribal organizations",
        },
    "education_level":
        {
        "public_and_state_institutions_of_higher_education":
            "Public/state institutions of higher education",
        "private_institutions_of_higher_education":
            "Private institutions of higher education",
        "independent_school_districts": "Independent school districts",
        },
    "nonprofit_status":
        {
        "nonprofits_non_higher_education_with_501c3":
            "Nonprofits (non-higher-ed) WITH 501(c)(3)",
        "nonprofits_non_higher_education_without_501c3":
            "Nonprofits (non-higher-ed) WITHOUT 501(c)(3)",
        "for_profit_organizations_other_than_small_businesses":
            "For-profit organizations (other than small businesses)",
        },
    "miscellaneous":
        {
        "small_businesses": "Small businesses",
        "public_and_indian_housing_authorities": "Public and Indian housing authorities",
        "individuals": "Individuals",
        "other": "Other",
        "unrestricted": "Unrestricted (open to anyone)",
        }
}

# --------------------------------------------------------------------------
# 2. DEFAULT FILE PATHS (used if not overridden via command line)
# --------------------------------------------------------------------------
INPUT_CSV = "grants-search-202608182008.csv"
OUTPUT_CSV = "grants-filtered.csv"

# --------------------------------------------------------------------------
# 3. APPLICANT PROFILE
#    Edit this to describe who is applying. Set each flag to True/False,
#    OR skip editing this entirely and run with --interactive instead.
# --------------------------------------------------------------------------
APPLICANT_PROFILE = {
    "state_governments": False,
    "county_governments": False,
    "city_or_township_governments": False,
    "special_district_governments": False,
    "federally_recognized_native_american_tribal_governments": False,
    "other_native_american_tribal_organizations": False,
    "public_and_state_institutions_of_higher_education": False,
    "private_institutions_of_higher_education": False,
    "independent_school_districts": False,
    "nonprofits_non_higher_education_with_501c3": False,
    "nonprofits_non_higher_education_without_501c3": False,
    "for_profit_organizations_other_than_small_businesses": False,
    "small_businesses": False,
    "public_and_indian_housing_authorities": False,
    "individuals": False,
}


def build_profile_interactively():
    """Ask the user yes/no questions to build their eligibility profile."""
    print("\nAnswer 'y' or 'n' for each category that applies to you.\n")
    profile = {}
    for section in ALL_APPLICANT_TYPES:
        profile[section] = {}
        for key, label in ALL_APPLICANT_TYPES[section].items():
            if key in ("other", "unrestricted"):
                continue
            if section != "miscellaneous" and ( True in profile[section].values() ):
                profile[section][key] = False
                continue
            while True:
                ans = input(f"Are you a: {label}? [y/n] ").strip().lower()
                if ans in ("y", "yes"):
                    profile[section][key] = True
                    break
                elif ans in ("n", "no"):
                    profile[section][key] = False
                    break
                print("  Please answer y or n.")

    return profile


def get_eligible_codes(profile):
    """Return the set of applicant_type codes this applicant matches."""
    codes = {key for key, is_me in profile.items() if is_me}
    # Opportunities marked "unrestricted" are open to everyone.
    codes.add("unrestricted")
    return codes


def is_eligible(applicant_types_cell, eligible_codes):
    """
    Check a single row's applicant_types cell (semicolon-separated string)
    against the set of codes the applicant matches.
    """
    if pd.isna(applicant_types_cell) or not str(applicant_types_cell).strip():
        # No eligibility info listed -> can't confirm exclusion, so keep it
        # for manual review. Change to `return False` to exclude instead.
        return True

    row_types = {t.strip() for t in str(applicant_types_cell).split(";") if t.strip()}
    return bool(row_types & eligible_codes)


def filter_grants(input_csv, output_csv, profile):
    eligible_codes = get_eligible_codes(profile)

    if not (eligible_codes - {"unrestricted"}):
        print(
            "\nWarning: no applicant categories were selected — only "
            "'unrestricted' opportunities will be kept.\n"
        )

    print(f"Reading: {input_csv}")
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=True)

    if "applicant_types" not in df.columns:
        sys.exit("Error: expected column 'applicant_types' not found in the CSV.")

    mask = df["applicant_types"].apply(lambda cell: is_eligible(cell, eligible_codes))
    filtered = df[mask]

    """ export filtered dataset """
    # filtered.to_csv(output_csv, index=False)

    print(f"\nApplicant profile matched codes: {sorted(eligible_codes)}")
    print(f"Total opportunities in input:    {len(df)}")
    print(f"Eligible opportunities kept:      {len(filtered)}")
    print(f"Excluded (not eligible):          {len(df) - len(filtered)}")
    print(f"Saved filtered results to:        {output_csv}")


def main():
    parser = argparse.ArgumentParser(description="Filter grants by applicant eligibility.")
    parser.add_argument("input_csv", nargs="?", default=INPUT_CSV,
                         help="Path to the source grants CSV.")
    parser.add_argument("output_csv", nargs="?", default=OUTPUT_CSV,
                         help="Path to write the filtered CSV.")
    parser.add_argument("--interactive", action="store_true",
                         help="Answer prompts to build your applicant profile "
                              "instead of using APPLICANT_PROFILE in the script.")
    args = parser.parse_args()

    profile = build_profile_interactively() if args.interactive else APPLICANT_PROFILE

    filter_grants(args.input_csv, args.output_csv, profile)


if __name__ == "__main__":
    main()