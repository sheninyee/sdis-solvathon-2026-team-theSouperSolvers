# Deployment Guide — Federal Funds Matchmaker

> **Never commit secrets.** All keys and connection strings live only in environment variables.

---

## Prerequisites

- Azure CLI logged in (`az login`) with access to `rg-primary-unc-sdis-solveathon-team12`
- Docker Desktop running (for local test) OR just use `az acr build` (builds in cloud)
- Python 3.12 (for local test)

---

## 1. Local demo run (no Azure needed)

```bash
cd <repo_root>
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# Open http://localhost:8000
```

The app falls back to `data/sample/scored_ncdot_latest.json` automatically.

---

## 2. Build and push Docker image to Azure Container Registry

```bash
# Variables — set these once in your shell
RG=rg-primary-unc-sdis-solveathon-team12
ACR=acrffm13252
APP_NAME=ffm-app
JOB_NAME=ffm-job
LOCATION=eastus

# Build in the cloud (no local Docker required)
az acr build \
  --registry $ACR \
  --image ffm:latest \
  --resource-group $RG \
  .
```

---

## 3. Set secret environment variables (do this once)

```bash
# Get your storage connection string from the portal, then:
STORAGE_CONN="<paste connection string from Azure portal>"
AOAI_ENDPOINT="<your Azure OpenAI endpoint>"
AOAI_KEY="<your Azure OpenAI key>"
APP_ACCESS_CODE="<choose a strong passphrase>"
```

---

## 4. Deploy the web app (Container App)

```bash
az containerapp create \
  --name $APP_NAME \
  --resource-group $RG \
  --image $ACR.azurecr.io/ffm:latest \
  --registry-server $ACR.azurecr.io \
  --env-vars \
    MODE=app \
    STORAGE_CONN="$STORAGE_CONN" \
    AOAI_ENDPOINT="$AOAI_ENDPOINT" \
    AOAI_KEY="$AOAI_KEY" \
    APP_ACCESS_CODE="$APP_ACCESS_CODE" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 2
```

After this prints a URL, **note it** — that's your live app.

---

## 5. Seed initial data to blob

```bash
STORAGE_CONN="$STORAGE_CONN" python seed.py
```

This uploads `profiles/ncdot.json`, `accounts/ncdot.json`, and the sample scored data.
The app will now show 8 real-looking opportunities without needing to run a pipeline job.

---

## 6. Run the pipeline job manually (gets real Grants.gov data)

```bash
az containerapp job create \
  --name $JOB_NAME \
  --resource-group $RG \
  --image $ACR.azurecr.io/ffm:latest \
  --registry-server $ACR.azurecr.io \
  --env-vars \
    MODE=job \
    STORAGE_CONN="$STORAGE_CONN" \
    AOAI_ENDPOINT="$AOAI_ENDPOINT" \
    AOAI_KEY="$AOAI_KEY" \
  --replica-timeout 600 \
  --trigger-type Manual

# Then fire it:
az containerapp job start --name $JOB_NAME --resource-group $RG
```

The job runs `python job.py`, fetches live Grants.gov data, scores it, and writes
`scored/ncdot/latest.json` to blob. The web app reads this on the next page load.

---

## 7. Update after code change

```bash
# Rebuild image
az acr build --registry $ACR --image ffm:latest --resource-group $RG .

# Update the Container App to pull the new image
az containerapp update --name $APP_NAME --resource-group $RG \
  --image $ACR.azurecr.io/ffm:latest
```

---

## Gotchas

| Problem | Fix |
|---|---|
| App shows "0 opportunities" | Run `seed.py` to load sample data into blob |
| Scores all show "Low confidence" | Expected in `keyword_fallback` mode (no AOAI key). Set `AOAI_ENDPOINT` + `AOAI_KEY` to enable semantic scoring. |
| Job fails after 10 min | Grants.gov sometimes slow. Set `--replica-timeout 900` |
| Container App URL not found | Wait 2 min after create; check `az containerapp show --name $APP_NAME -g $RG` |
