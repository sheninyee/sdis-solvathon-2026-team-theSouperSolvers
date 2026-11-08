#!/usr/bin/env python3
"""
extract_profile_from_pdf.py
───────────────────────────
Reads a NC agency Strategic Plan PDF and uses GPT-4.1-mini to produce
a profiles/{account_id}.json file for the Federal Funds Matchmaker.

Usage:
    python scripts/extract_profile_from_pdf.py \
        --pdf "Strategic Plans/NC DEQ Strategic Plan.pdf" \
        --account-id ncdeq \
        --org-name "NC Department of Environmental Quality"

Requirements:
    pip install pypdf openai  (or use existing project venv)

Environment vars (same as the app):
    AOAI_ENDPOINT, AOAI_KEY, AOAI_CHAT_MODEL (default: chat)
"""

import argparse, json, os, re, sys
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader   # older name
    except ImportError:
        sys.exit("Install pypdf:  pip install pypdf")


def extract_text(pdf_path: Path, max_chars: int = 20_000) -> str:
    """Extract up to max_chars of text from a PDF, prioritising early pages."""
    reader = PdfReader(str(pdf_path))
    chunks = []
    total = 0
    for page in reader.pages:
        t = (page.extract_text() or "").strip()
        if not t:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        chunks.append(t[:remaining])
        total += len(t)
    return "\n\n".join(chunks)


def call_gpt(text: str, account_id: str, org_name: str) -> dict:
    endpoint = os.environ.get("AOAI_ENDPOINT", "").rstrip("/")
    key = os.environ.get("AOAI_KEY", "")
    model = os.environ.get("AOAI_CHAT_MODEL", "chat")

    if not endpoint or not key:
        sys.exit("Set AOAI_ENDPOINT and AOAI_KEY environment variables")

    prompt = f"""You are a grants intelligence assistant.

Below is text extracted from the strategic plan of "{org_name}" (account_id: {account_id}).

Your task: generate a JSON profile describing its strategic priorities for federal grant matching.

--- STRATEGIC PLAN TEXT (truncated) ---
{text[:18000]}
--- END ---

Return ONLY valid JSON matching this exact schema:
{{
  "account_id": "{account_id}",
  "org_name": "{org_name}",
  "profile_review_status": "pdf-extracted",
  "matching_units": [
    {{
      "id": "short_snake_case_id",
      "title": "Strategic Goal Title (from the plan)",
      "keywords_plan": ["exact phrase from plan", ...],
      "keywords_expansion": ["related federal grant term", ...]
    }}
  ]
}}

Rules:
- 5-10 matching_units derived directly from goals/pillars/strategies in the document
- keywords_plan: exact phrases or near-exact from the plan (6-12 each)
- keywords_expansion: related federal RFA terminology not in the plan (8-15 each)
- Do NOT invent goals; only use what the document supports
- Return only the JSON object, no markdown, no explanation
"""

    import urllib.request
    url = f"{endpoint}/openai/deployments/{model}/chat/completions?api-version=2024-12-01-preview"
    payload = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 3000,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "api-key": key,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    text_out = body["choices"][0]["message"]["content"]
    # strip markdown fences
    text_out = re.sub(r"```(?:json)?", "", text_out).strip().strip("`").strip()
    return json.loads(text_out)


def main():
    p = argparse.ArgumentParser(description="Extract strategic plan profile from PDF")
    p.add_argument("--pdf", required=True, help="Path to the Strategic Plan PDF")
    p.add_argument("--account-id", required=True, help="Short account id, e.g. ncdeq")
    p.add_argument("--org-name", required=True, help="Full org name")
    p.add_argument("--out-dir", default="profiles", help="Output directory (default: profiles/)")
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"PDF not found: {pdf_path}")

    print(f"📄  Extracting text from {pdf_path.name} …")
    text = extract_text(pdf_path)
    print(f"    Got {len(text):,} characters")

    print(f"🤖  Calling GPT ({os.environ.get('AOAI_CHAT_MODEL', 'chat')}) …")
    profile = call_gpt(text, args.account_id, args.org_name)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.account_id}.json"
    out_path.write_text(json.dumps(profile, indent=2))
    print(f"✅  Saved → {out_path}")
    print(f"    Units: {len(profile.get('matching_units', []))}")


if __name__ == "__main__":
    main()
