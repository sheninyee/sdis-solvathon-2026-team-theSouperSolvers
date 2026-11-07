"""seed.py — Seed the NCDOT account and profile to Azure Blob Storage.

Run once during initial deployment or to reset to defaults.

Usage:
  STORAGE_CONN="<connection_string>" python seed.py

Creates in blob container 'ffm-data':
  accounts/ncdot.json  — account record
  profiles/ncdot.json  — NCDOT scoring profile
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


STORAGE_CONN = os.environ.get("STORAGE_CONN", "")
CONTAINER = "ffm-data"


def _blob_client(blob_path: str):
    if not STORAGE_CONN:
        raise RuntimeError("STORAGE_CONN environment variable is not set")
    from azure.storage.blob import BlobServiceClient
    svc = BlobServiceClient.from_connection_string(STORAGE_CONN)
    return svc.get_blob_client(CONTAINER, blob_path)


def _ensure_container():
    if not STORAGE_CONN:
        return
    from azure.storage.blob import BlobServiceClient
    svc = BlobServiceClient.from_connection_string(STORAGE_CONN)
    cc = svc.get_container_client(CONTAINER)
    try:
        cc.create_container()
        print(f"Created container: {CONTAINER}")
    except Exception:
        print(f"Container already exists: {CONTAINER}")


def upload(blob_path: str, obj: dict):
    bc = _blob_client(blob_path)
    bc.upload_blob(json.dumps(obj, indent=2), overwrite=True)
    print(f"  Uploaded: {blob_path}")


def seed_account():
    account = {
        "account_id": "ncdot",
        "display_name": "NC Department of Transportation",
        "type": "department",
        "owner_user_id": "demo",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_status": "ai-draft",
    }
    upload("accounts/ncdot.json", account)
    return account


def seed_profile():
    profile_path = Path("profiles/ncdot.json")
    if not profile_path.exists():
        print("ERROR: profiles/ncdot.json not found. Run from repo root.")
        sys.exit(1)
    profile = json.loads(profile_path.read_text())
    upload("profiles/ncdot.json", profile)
    return profile


def seed_sample_data():
    """Seed the demo scored data so the UI works without a job run."""
    sample_path = Path("data/sample/scored_ncdot_latest.json")
    if not sample_path.exists():
        print("  Skipping sample data — data/sample/scored_ncdot_latest.json not found")
        return
    data = json.loads(sample_path.read_text())
    upload("scored/ncdot/latest.json", data)


def main():
    if not STORAGE_CONN:
        print("ERROR: STORAGE_CONN env var not set.")
        print("Usage: STORAGE_CONN='<connection_string>' python seed.py")
        sys.exit(1)

    print("=== Seeding FFM data to Azure Blob ===")
    _ensure_container()

    print("\nSeeding account...")
    seed_account()

    print("\nSeeding profile...")
    seed_profile()

    print("\nSeeding sample scored data...")
    seed_sample_data()

    print("\nDone. The app should now load without a job run.")


if __name__ == "__main__":
    main()
