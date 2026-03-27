#!/usr/bin/env python3
"""Create the CHITRA Tax Tracker Google Sheet with 3 tabs."""

import json
import urllib.request

from config_loader import refresh_access_token


def api_request(url, token, method="GET", data=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    token = refresh_access_token()

    spreadsheet_body = {
        "properties": {"title": "CHITRA Tax Tracker 2025"},
        "sheets": [
            {
                "properties": {
                    "title": "2025 Document Checklist",
                    "index": 0,
                    "gridProperties": {"frozenRowCount": 1},
                }
            },
            {
                "properties": {
                    "title": "2024 Return Summary",
                    "index": 1,
                    "gridProperties": {"frozenRowCount": 1},
                }
            },
            {
                "properties": {
                    "title": "2025 Changes Log",
                    "index": 2,
                    "gridProperties": {"frozenRowCount": 1},
                }
            },
        ],
    }

    result = api_request(
        "https://sheets.googleapis.com/v4/spreadsheets",
        token,
        method="POST",
        data=spreadsheet_body,
    )

    spreadsheet_id = result["spreadsheetId"]
    url = result["spreadsheetUrl"]
    print(f"SPREADSHEET_ID={spreadsheet_id}")
    print(f"URL={url}")


if __name__ == "__main__":
    main()
