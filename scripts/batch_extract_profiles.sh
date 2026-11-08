#!/bin/bash
# batch_extract_profiles.sh
# Run from the repo root to generate profiles for all Strategic Plan PDFs.
# Usage:  bash scripts/batch_extract_profiles.sh "Strategic Plans"
#
# Requires: AOAI_ENDPOINT and AOAI_KEY set (source .env first).

PLANS_DIR="${1:-Strategic Plans}"
SCRIPT="scripts/extract_profile_from_pdf.py"

if [[ ! -d "$PLANS_DIR" ]]; then
  echo "❌  Directory not found: $PLANS_DIR"
  exit 1
fi

declare -A KNOWN_IDS=(
  # Pattern match → account_id,org_name
  ["DEQ"]="ncdeq|NC Department of Environmental Quality"
  ["DIT"]="ncdit|NC Department of Information Technology"
  ["DACS"]="ncdacs|NC Department of Agriculture and Consumer Services"
  ["DMVA"]="ncdmva|NC Department of Military and Veterans Affairs"
  ["DOL"]="ncdol|NC Department of Labor"
  ["DPS"]="ncdps|NC Department of Public Safety"
  ["DPI"]="ncdpi|NC Department of Public Instruction"
  ["DHHS"]="ncdhhs|NC Department of Health and Human Services"
  ["OSBM"]="ncosbm|NC Office of State Budget and Management"
  ["OAH"]="ncoah|NC Office of Administrative Hearings"
  ["DOT"]="ncdot|NC Department of Transportation"
  ["Commerce"]="nccommerce|NC Department of Commerce"
  ["Revenue"]="ncdor|NC Department of Revenue"
  ["Treasurer"]="nctreasurer|NC Department of State Treasurer"
  ["Secretary of State"]="ncsos|NC Secretary of State"
)

for pdf in "$PLANS_DIR"/*.pdf; do
  fname=$(basename "$pdf")
  matched_id=""
  matched_name=""
  for key in "${!KNOWN_IDS[@]}"; do
    if [[ "$fname" == *"$key"* ]] || [[ "${fname^^}" == *"${key^^}"* ]]; then
      IFS="|" read -r matched_id matched_name <<< "${KNOWN_IDS[$key]}"
      break
    fi
  done

  if [[ -z "$matched_id" ]]; then
    echo "⚠️   Skipping (unknown mapping): $fname"
    continue
  fi

  if [[ -f "profiles/${matched_id}.json" ]]; then
    echo "✓   Already exists: profiles/${matched_id}.json  (skipping; delete to re-extract)"
    continue
  fi

  echo "→   Extracting: $fname  →  profiles/${matched_id}.json"
  python "$SCRIPT" \
    --pdf "$pdf" \
    --account-id "$matched_id" \
    --org-name "$matched_name"
done

echo ""
echo "Done. Review each profiles/*.json and adjust keywords as needed."
