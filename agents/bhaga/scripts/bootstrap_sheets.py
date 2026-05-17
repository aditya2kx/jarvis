#!/usr/bin/env python3
"""BHAGA one-time bootstrap: create the three Google Sheets workbooks for a store.

For a brand-new store, this creates (in the store's Google account):
    1. A folder named 'BHAGA' at My Drive root (if not already present).
    2. Three spreadsheets inside that folder:
       - BHAGA ADP Raw    (raw shift punches, wage rates)
       - BHAGA Square Raw (raw transactions)
       - BHAGA Model      (IMPORTRANGE-joined model + charts)
    3. Each spreadsheet seeded with placeholder tabs matching the M1 schema
       so the M1 ledger-writer slot-in cleanly.

Outputs a JSON dict that the operator pastes into the store profile at
agents/bhaga/knowledge-base/store-profiles/{store}.json.

Idempotency:
    * Folder creation is idempotent (skips if a folder named 'BHAGA' already
      exists at root).
    * Spreadsheet creation is NOT idempotent. Re-running creates new files.
      Pass --reuse-existing to look up by exact title in the BHAGA folder
      and re-use them instead of creating duplicates.

Usage:
    python3 -m agents.bhaga.scripts.bootstrap_sheets --store palmetto
    python3 -m agents.bhaga.scripts.bootstrap_sheets --store palmetto --reuse-existing
    python3 -m agents.bhaga.scripts.bootstrap_sheets --store palmetto --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core.config_loader import refresh_access_token
from skills.tip_ledger_writer.schema import WORKBOOK_SCHEMAS


DRIVE_API = "https://www.googleapis.com/drive/v3"
SHEETS_API = "https://sheets.googleapis.com/v4"

FOLDER_NAME = "BHAGA"


# Schemas are imported from skills/tip_ledger_writer/schema.py so the
# runtime writer and this bootstrap script share a single source of truth.


# ── Drive / Sheets API helpers ────────────────────────────────────


def api_request(url: str, token: str, *, method: str = "GET", data: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}\n{err_body}") from None


def find_folder_at_root(token: str, name: str) -> str | None:
    """Return folder id if a non-trashed folder of given name exists at My Drive root, else None."""
    q = (
        f"mimeType='application/vnd.google-apps.folder' "
        f"and name='{name}' "
        f"and 'root' in parents "
        f"and trashed=false"
    )
    url = f"{DRIVE_API}/files?q={urllib.parse.quote(q)}&fields=files(id,name,parents)"
    resp = api_request(url, token)
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def find_spreadsheet_in_folder(token: str, folder_id: str, title: str) -> str | None:
    """Return spreadsheet id if a non-trashed file with given title exists in folder, else None."""
    q = (
        f"mimeType='application/vnd.google-apps.spreadsheet' "
        f"and name='{title}' "
        f"and '{folder_id}' in parents "
        f"and trashed=false"
    )
    url = f"{DRIVE_API}/files?q={urllib.parse.quote(q)}&fields=files(id,name,parents)"
    resp = api_request(url, token)
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def create_folder_at_root(token: str, name: str) -> str:
    resp = api_request(
        f"{DRIVE_API}/files",
        token,
        method="POST",
        data={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["root"],
        },
    )
    return resp["id"]


def create_spreadsheet(token: str, title: str, tab_specs: list[dict]) -> dict:
    """Create a spreadsheet with the given title and seed tabs.

    Each tab_spec has:
        tab_name: tab title
        header:   list[str] -- row 1, frozen
        notes:    str -- displayed in cell A1 of a sibling _meta tab (optional)
    """
    body = {
        "properties": {"title": title},
        "sheets": [
            {
                "properties": {
                    "title": spec["tab_name"],
                    "index": i,
                    "gridProperties": {
                        "frozenRowCount": 1,
                        "columnCount": max(26, len(spec["header"]) + 4),
                    },
                }
            }
            for i, spec in enumerate(tab_specs)
        ],
    }
    return api_request(f"{SHEETS_API}/spreadsheets", token, method="POST", data=body)


def move_file_into_folder(token: str, file_id: str, folder_id: str) -> None:
    """Move file to folder by replacing its parents."""
    # Get current parents to compute removeParents
    info = api_request(f"{DRIVE_API}/files/{file_id}?fields=parents", token)
    current_parents = ",".join(info.get("parents", []))
    url = (
        f"{DRIVE_API}/files/{file_id}"
        f"?addParents={folder_id}"
        f"&removeParents={current_parents}"
        f"&fields=id,parents"
    )
    api_request(url, token, method="PATCH", data={})


def seed_tab_headers(token: str, spreadsheet_id: str, tab_specs: list[dict]) -> None:
    """Batch-write header rows + a notes comment into each tab.

    Uses values.batchUpdate to set row 1 of each tab to the header list.
    Notes are written into cell N1 (one column past the headers) as a freeform
    'how-to-use' string, so the operator opening the sheet sees usage context
    without us creating a separate _meta tab.
    """
    data = []
    for spec in tab_specs:
        tab = spec["tab_name"]
        header = spec["header"]
        data.append({
            "range": f"{tab}!A1:{_col_letter(len(header))}1",
            "values": [header],
        })
        notes_col = _col_letter(len(header) + 2)
        data.append({
            "range": f"{tab}!{notes_col}1",
            "values": [[spec.get("notes", "")]],
        })
    api_request(
        f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values:batchUpdate",
        token,
        method="POST",
        data={"valueInputOption": "RAW", "data": data},
    )


def _col_letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


# ── Main bootstrap ────────────────────────────────────────────────


def bootstrap(
    *,
    store: str,
    dry_run: bool = False,
    reuse_existing: bool = False,
) -> dict:
    """Create or reuse the BHAGA folder and three workbooks. Return a summary dict."""
    print(f"# BHAGA bootstrap for store={store!r} dry_run={dry_run} reuse_existing={reuse_existing}")
    token = refresh_access_token(account=store)
    print(f"  obtained access token (len={len(token)})")

    # Step 1: folder
    folder_id = find_folder_at_root(token, FOLDER_NAME)
    if folder_id:
        print(f"  found existing folder {FOLDER_NAME!r}: {folder_id}")
    elif dry_run:
        print(f"  DRY: would create folder {FOLDER_NAME!r} at My Drive root")
        folder_id = "<dry-run-folder-id>"
    else:
        folder_id = create_folder_at_root(token, FOLDER_NAME)
        print(f"  created folder {FOLDER_NAME!r}: {folder_id}")

    # Step 2: spreadsheets
    workbooks: dict[str, dict] = {}
    for title, tab_specs in WORKBOOK_SCHEMAS.items():
        existing_id = (
            find_spreadsheet_in_folder(token, folder_id, title)
            if (folder_id and not dry_run)
            else None
        )
        if existing_id and reuse_existing:
            print(f"  reuse: {title!r} -> {existing_id}")
            workbooks[title] = {
                "spreadsheet_id": existing_id,
                "url": f"https://docs.google.com/spreadsheets/d/{existing_id}/edit",
                "tabs": [s["tab_name"] for s in tab_specs],
                "action": "reused",
            }
            continue
        if existing_id and not reuse_existing:
            print(
                f"  WARNING: spreadsheet {title!r} already exists at {existing_id}. "
                "Pass --reuse-existing to use it, or rename/delete it first. Skipping."
            )
            workbooks[title] = {
                "spreadsheet_id": existing_id,
                "url": f"https://docs.google.com/spreadsheets/d/{existing_id}/edit",
                "tabs": [s["tab_name"] for s in tab_specs],
                "action": "skipped_already_exists",
            }
            continue
        if dry_run:
            print(f"  DRY: would create spreadsheet {title!r} with tabs={[s['tab_name'] for s in tab_specs]}")
            workbooks[title] = {
                "spreadsheet_id": "<dry-run-id>",
                "url": "<dry-run-url>",
                "tabs": [s["tab_name"] for s in tab_specs],
                "action": "would_create",
            }
            continue
        info = create_spreadsheet(token, title, tab_specs)
        sid, url = info["spreadsheetId"], info["spreadsheetUrl"]
        print(f"  created spreadsheet {title!r}: {sid}")
        move_file_into_folder(token, sid, folder_id)
        seed_tab_headers(token, sid, tab_specs)
        workbooks[title] = {
            "spreadsheet_id": sid,
            "url": url,
            "tabs": [s["tab_name"] for s in tab_specs],
            "action": "created",
        }

    # Step 3: emit the store-profile fragment.
    snippet = {
        "store": store,
        "google_drive": {
            "bhaga_folder_id": folder_id,
            "bhaga_folder_name": FOLDER_NAME,
        },
        "google_sheets": {
            "bhaga_adp_raw_id": workbooks.get("BHAGA ADP Raw", {}).get("spreadsheet_id"),
            "bhaga_square_raw_id": workbooks.get("BHAGA Square Raw", {}).get("spreadsheet_id"),
            "bhaga_model_id": workbooks.get("BHAGA Model", {}).get("spreadsheet_id"),
        },
        "workbooks_detail": workbooks,
        "bootstrap_run_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return snippet


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--store", required=True,
        help="Store / Google account name (matches config.yaml accounts.{store}).",
    )
    cli.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be created without touching Drive/Sheets.",
    )
    cli.add_argument(
        "--reuse-existing", action="store_true",
        help="If a workbook with the same title exists in BHAGA folder, reuse it instead of creating a duplicate.",
    )
    cli.add_argument(
        "--out", default=None,
        help=("Path to write the resulting JSON snippet. Defaults to "
              "agents/bhaga/knowledge-base/store-profiles/{store}.bootstrap.json."),
    )
    args = cli.parse_args()

    snippet = bootstrap(
        store=args.store,
        dry_run=args.dry_run,
        reuse_existing=args.reuse_existing,
    )

    print()
    print("=" * 70)
    print("Store-profile snippet (write into agents/bhaga/knowledge-base/store-profiles/{store}.json):")
    print(json.dumps(snippet, indent=2))

    # __file__ is at agents/bhaga/scripts/bootstrap_sheets.py -- go up 4 dirs to reach project root.
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    out_path = (
        args.out
        if args.out
        else os.path.join(
            project_root, "agents", "bhaga", "knowledge-base",
            "store-profiles", f"{args.store}.bootstrap.json",
        )
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(snippet, f, indent=2)
    print(f"\nSnippet also written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
