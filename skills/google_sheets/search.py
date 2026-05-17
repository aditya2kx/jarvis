#!/usr/bin/env python3
"""Search for Google Sheets files in Google Drive."""

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import refresh_access_token

__all__ = ["search_spreadsheets", "list_recent_spreadsheets"]

DRIVE_API = "https://www.googleapis.com/drive/v3/files"


def _api_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def search_spreadsheets(query_text, account=None, max_results=20):
    """Search Drive for spreadsheets matching a text query.

    The query_text is matched against the file name (uses Drive's fullText search).
    Only returns Google Sheets files.
    """
    token = refresh_access_token(account=account)

    q = f"mimeType='application/vnd.google-apps.spreadsheet' and fullText contains '{query_text}' and trashed=false"
    params = urllib.parse.urlencode({
        "q": q,
        "pageSize": max_results,
        "fields": "files(id,name,modifiedTime,owners,webViewLink)",
        "orderBy": "modifiedTime desc",
    })
    url = f"{DRIVE_API}?{params}"
    data = _api_get(url, token)

    return [{
        "id": f["id"],
        "name": f["name"],
        "modifiedTime": f.get("modifiedTime", ""),
        "owner": f.get("owners", [{}])[0].get("emailAddress", ""),
        "webViewLink": f.get("webViewLink", ""),
    } for f in data.get("files", [])]


def list_recent_spreadsheets(account=None, max_results=20):
    """List recently modified spreadsheets."""
    token = refresh_access_token(account=account)

    q = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    params = urllib.parse.urlencode({
        "q": q,
        "pageSize": max_results,
        "fields": "files(id,name,modifiedTime,owners,webViewLink)",
        "orderBy": "modifiedTime desc",
    })
    url = f"{DRIVE_API}?{params}"
    data = _api_get(url, token)

    return [{
        "id": f["id"],
        "name": f["name"],
        "modifiedTime": f.get("modifiedTime", ""),
        "owner": f.get("owners", [{}])[0].get("emailAddress", ""),
        "webViewLink": f.get("webViewLink", ""),
    } for f in data.get("files", [])]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Search Google Sheets")
    parser.add_argument("query", nargs="?", help="Search text (omit to list recent)")
    parser.add_argument("--account", default=None)
    parser.add_argument("--max", type=int, default=10)
    args = parser.parse_args()

    if args.query:
        results = search_spreadsheets(args.query, account=args.account, max_results=args.max)
    else:
        results = list_recent_spreadsheets(account=args.account, max_results=args.max)

    for r in results:
        print(f"  {r['name']}")
        print(f"    ID: {r['id']}")
        print(f"    Modified: {r['modifiedTime']}  Owner: {r['owner']}")
        print(f"    Link: {r['webViewLink']}")
        print()
