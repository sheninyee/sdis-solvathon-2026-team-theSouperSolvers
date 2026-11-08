# Federal Funds Matchmaker

> Built by the Souper Solvers — UNC Chapel Hill Data Science Solve-a-thon 2026
> NC SDIS Team 12 · focused on NC Department of Transportation

A web application that helps North Carolina state agencies discover and prioritize relevant federal grant opportunities. It pulls live data from Grants.gov, scores each grant against an agency's strategic plan, and surfaces the best-fit opportunities through a ranked, filterable interface.

**Active development branch:** `ninyee/finals-features` — all recent features are there, not on `main`.

---

## What it does

- Fetches and scores federal grants from Grants.gov daily (or on demand)
- Ranks grants by eligibility, semantic relevance, keyword match, timing, and funder affinity
- 3-panel UI: filters + ranked list | grant detail | project match
- AI-generated 3-part briefing per grant: Purpose / Fit / Watch out
- Project Match: upload a project doc or paste text → finds the most relevant grants
- Compare up to 10 grants and download as CSV
- Multi-agency accounts — each agency gets its own profile, decisions, and scoring
- Decision tracking (Pursue / Maybe / Pass) with notes
- Drag-to-resize panels, deep-links via URL hash (`#grant/<id>`)

---

## Repository layout

```
main.py          Web server (FastAPI). All API routes + AI calls.
job.py           Pipeline runner — fetches, normalizes, scores, writes results.
ingest.py        Pulls raw opportunities from Grants.gov API v2.
normalize.py     Cleans and maps Grants.gov fields to our canonical schema.
score.py         6-signal scoring engine (semantic, keyword, eligibility, timing, funder, attractiveness).
seed.py          One-time seeder — uploads default profiles and sample data to blob.
profiles/        Agency profiles (one JSON per agency). Controls scoring keywords + strategy.
data/sample/     Local fallback data (used when Azure Storage is unavailable).
static/          Frontend — index.html, app.js (SPA), style.css.
docs/DEPLOY.md   Step-by-step Azure deployment guide.
```

**Data flow:**
```
Grants.gov API
  → ingest.py      (raw opportunities)
  → normalize.py   (canonical schema)
  → score.py       (scored + ranked)
  → job.py         (orchestrates above, writes to blob or local JSON)
  → main.py        (serves scored JSON to the browser via /api/opportunities)
```

---

## Local development

### Prerequisites

- Python 3.12+
- `pip install -r requirements.txt` (includes `python-multipart` — required for file upload)

### Environment variables

Create a `.env` file (never commit it):

```
AOAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AOAI_KEY=<your-azure-openai-key>
AOAI_EMBEDDING_MODEL=embed
AOAI_CHAT_MODEL=chat
STORAGE_CONN=<azure-storage-connection-string>   # optional; falls back to local files
APP_ACCESS_CODE=<passphrase>                      # optional; leave blank for open access
```

All variables are optional for local testing. Without `AOAI_*` the app runs in `keyword_fallback` mode (scores still work, AI features degrade gracefully). Without `STORAGE_CONN` it reads from `data/sample/`.

### Run locally

```bash
# 1. Refresh grant data (optional — sample data already included)
python3 job.py

# 2. Start the server
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000>. Re-run `python3 job.py` any time to pull fresh Grants.gov data.

---

## Adding a new agency

1. Copy `profiles/ncdot.json` to `profiles/<agency_id>.json`
2. Edit `account_id`, `display_name`, and `matching_units` to reflect the new agency's strategic plan keywords and goals
3. The app will use this profile for scoring when `?account_id=<agency_id>` is passed, or when a user switches accounts in the UI
4. Run `python3 job.py` with `PROFILE_ID=<agency_id>` to generate a scored dataset for that agency

The `matching_units` array is the most important part — each unit maps a strategic goal to a set of keywords. The scoring engine uses these to rank grants by keyword overlap and (with Azure OpenAI) semantic similarity.

---

## Deploying to Azure

See [`docs/DEPLOY.md`](docs/DEPLOY.md) for the full step-by-step guide.

Short version:
```bash
RG=rg-primary-unc-sdis-solveathon-team12
ACR=acrffm13252

# Build and push image
az acr build --registry $ACR --image ffm:latest --resource-group $RG .

# Update the running Container App
az containerapp update --name ffm-app --resource-group $RG \
  --image $ACR.azurecr.io/ffm:latest
```

---

## AI configuration

| Feature | Requires | Fallback |
|---|---|---|
| Semantic scoring | `AOAI_ENDPOINT` + `AOAI_KEY` + embedding deployment | Keyword scoring only |
| Grant analysis (eligibility, pros/cons) | Same | "AI service not configured" message |
| AI fit briefing (Purpose / Fit / Watch out) | Same | Static fallback text |
| Project Match | Same | Keyword overlap matching |
| Agency profile generation | Same | Empty profile stub |

The app never fails hard without AI — it degrades to rule-based scoring throughout.

---

## Security rules (must not be broken)

- Never commit `.env`, API keys, connection strings, or raw upload data
- Keys and connection strings live only in environment variables and Container Apps secrets
- Never print or log secret values
- Never run `az group delete` or delete any resource without explicit human approval
- Only touch resources inside `rg-primary-unc-sdis-solveathon-team12`

---

## Known limitations / next steps

- Scoring is NCDOT-tuned; other agencies need their `profiles/*.json` reviewed by a human before trusting results
- The fit cache (`_fit_cache`) is in-memory — restarts clear it; a Redis or blob-backed cache would fix this for production
- Account creation persists to `profiles/` locally but not to blob on creation — `seed.py` would need to be extended
- The pipeline job (`job.py`) runs manually or on a Container Apps scheduled job; there is no automatic nightly trigger configured yet
- `app.py` and `index.html` in the root are prototype leftovers and can be deleted
