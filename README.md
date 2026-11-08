# sdis-solvathon-2026-team-theSouperSolvers
UNC Chapel Hill department of Data Science Solve-a-thon team The "Soup"er Solvers focused on NC DOT

## Run the website

Open app.py and change the port at line 4 to where your device is NOT currently running a different page
From this directory, run:

```bash
python3 app.py
```

Then open <http://localhost:<8000>> or whatever location you switched to, in a browser. Stop the server with `Ctrl+C`.

## Local development (Federal Funds Matchmaker)

### Prerequisites

```bash
pip3 install -r requirements.txt
```

### 1. Configure environment

Set your Azure OpenAI credentials in `.env`:

```
AOAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AOAI_KEY=<your-key>
AOAI_EMBEDDING_MODEL=embed
AOAI_CHAT_MODEL=chat
```

### 2. Run the data pipeline

Fetches grants from Grants.gov, scores them, and writes results to `data/sample/scored_ncdot_latest.json`:

```bash
python3 job.py
```

Expected output: `Pipeline complete in ~10s`, `mode=full` (or `mode=keyword_fallback` if OpenAI unavailable).

### 3. Start the web server

```bash
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000> in your browser. Stop with `Ctrl+C`.

Re-run `python3 job.py` any time to refresh grant data, then reload the page.
