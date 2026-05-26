#!/usr/bin/env python3
"""Share BHAGA Google Sheets with the Cloud Run service account.

One-time (idempotent) utility. Grants the service account Editor access
on every staging AND production sheet so the Cloud Run Job can read/write
via Application Default Credentials.

Usage:
    python3 -m agents.bhaga.scripts.share_sheets_with_sa --store palmetto
    python3 -m agents.bhaga.scripts.share_sheets_with_sa --store palmetto --staging-only
    python3 -m agents.bhaga.scripts.share_sheets_with_sa --store palmetto --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core.config_loader import refresh_access_token

SERVICE_ACCOUNT = "bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com"
DRIVE_API = "https://www.googleapis.com/drive/v3"


def _share_file(file_id: str, email: str, role: str, token: str) -> dict:
    """Grant a permission on a Drive file. Idempotent — Google dedupes."""
    url = f"{DRIVE_API}/files/{file_id}/permissions"
    body = json.dumps({
        "type": "user",
        "role": role,
        "emailAddress": email,
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode(errors="replace")
        return {"error": True, "code": e.code, "body": err, "file_id": file_id}


def main():
    cli = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--sa-email", default=SERVICE_ACCOUNT,
                     help="Service account email to grant access to.")
    cli.add_argument("--role", default="writer", choices=["reader", "writer"],
                     help="Permission role (default: writer).")
    cli.add_argument("--staging-only", action="store_true",
                     help="Only share staging sheets, skip production.")
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args()

    profile_path = os.path.join(
        os.path.dirname(__file__), "..", "knowledge-base", "store-profiles",
        f"{args.store}.json",
    )
    with open(profile_path) as f:
        profile = json.load(f)

    account = profile.get("google_account_key", args.store)
    token = refresh_access_token(account)

    sheets_to_share: list[tuple[str, str]] = []

    staging = profile.get("google_sheets_staging", {})
    for key, val in staging.items():
        if key.startswith("_"):
            continue
        sid = val.get("spreadsheet_id", "")
        if sid:
            sheets_to_share.append((f"staging/{key}", sid))

    if not args.staging_only:
        prod = profile.get("google_sheets", {})
        for key, val in prod.items():
            if key.startswith("_"):
                continue
            sid = val.get("spreadsheet_id", "")
            if sid:
                sheets_to_share.append((f"prod/{key}", sid))

    print(f"Sharing {len(sheets_to_share)} sheet(s) with {args.sa_email} (role={args.role})")
    for label, sid in sheets_to_share:
        if args.dry_run:
            print(f"  [DRY RUN] {label}: {sid}")
            continue
        result = _share_file(sid, args.sa_email, args.role, token)
        if result.get("error"):
            print(f"  FAIL {label}: HTTP {result['code']} — {result['body'][:200]}")
        else:
            print(f"  OK   {label}: {sid} → permission {result.get('id', '?')}")

    print("Done.")


if __name__ == "__main__":
    main()
