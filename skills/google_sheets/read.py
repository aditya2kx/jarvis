#!/usr/bin/env python3
"""Read data from Google Sheets — ranges, metadata, and full-sheet dumps."""

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import refresh_access_token

__all__ = ["read_range", "read_multiple_ranges", "get_spreadsheet_metadata", "read_all_sheets"]

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


def _api_get(url, token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def read_range(spreadsheet_id, range_a1, account=None):
    """Read a single A1-notation range (e.g. 'Sheet1!A1:C10').

    Returns a list of rows, where each row is a list of cell values.
    """
    token = refresh_access_token(account=account)
    encoded_range = urllib.parse.quote(range_a1, safe="")
    url = f"{SHEETS_API}/{spreadsheet_id}/values/{encoded_range}"
    data = _api_get(url, token)
    return data.get("values", [])


def read_multiple_ranges(spreadsheet_id, ranges, account=None):
    """Read multiple A1-notation ranges in one call.

    Returns a dict mapping each range to its rows.
    """
    token = refresh_access_token(account=account)
    params = "&".join(f"ranges={urllib.parse.quote(r, safe='')}" for r in ranges)
    url = f"{SHEETS_API}/{spreadsheet_id}/values:batchGet?{params}"
    data = _api_get(url, token)

    result = {}
    for vr in data.get("valueRanges", []):
        result[vr["range"]] = vr.get("values", [])
    return result


def get_spreadsheet_metadata(spreadsheet_id, account=None):
    """Get spreadsheet metadata — title, sheet names, row/col counts."""
    token = refresh_access_token(account=account)
    url = f"{SHEETS_API}/{spreadsheet_id}?fields=properties.title,sheets.properties"
    data = _api_get(url, token)

    sheets_info = []
    for s in data.get("sheets", []):
        props = s.get("properties", {})
        sheets_info.append({
            "sheetId": props.get("sheetId"),
            "title": props.get("title", ""),
            "index": props.get("index"),
            "rowCount": props.get("gridProperties", {}).get("rowCount"),
            "columnCount": props.get("gridProperties", {}).get("columnCount"),
        })

    return {
        "title": data.get("properties", {}).get("title", ""),
        "sheets": sheets_info,
    }


def read_all_sheets(spreadsheet_id, account=None):
    """Read every sheet in a spreadsheet. Returns dict of {sheet_title: rows}."""
    meta = get_spreadsheet_metadata(spreadsheet_id, account=account)
    ranges = [f"'{s['title']}'!A:ZZ" for s in meta["sheets"]]
    if not ranges:
        return {}
    raw = read_multiple_ranges(spreadsheet_id, ranges, account=account)
    result = {}
    for sheet in meta["sheets"]:
        for range_key, rows in raw.items():
            if sheet["title"] in range_key:
                result[sheet["title"]] = rows
                break
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read Google Sheets data")
    parser.add_argument("spreadsheet_id", help="Spreadsheet ID")
    parser.add_argument("--range", help="A1 range (e.g. 'Sheet1!A1:D10')")
    parser.add_argument("--all", action="store_true", help="Read all sheets")
    parser.add_argument("--meta", action="store_true", help="Show metadata only")
    parser.add_argument("--account", default=None)
    args = parser.parse_args()

    if args.meta:
        meta = get_spreadsheet_metadata(args.spreadsheet_id, account=args.account)
        print(f"Title: {meta['title']}")
        for s in meta["sheets"]:
            print(f"  Sheet: {s['title']} ({s['rowCount']}x{s['columnCount']})")
    elif args.all:
        data = read_all_sheets(args.spreadsheet_id, account=args.account)
        for sheet_name, rows in data.items():
            print(f"\n=== {sheet_name} ({len(rows)} rows) ===")
            for row in rows[:5]:
                print("  " + " | ".join(str(c) for c in row))
            if len(rows) > 5:
                print(f"  ... ({len(rows) - 5} more rows)")
    elif args.range:
        rows = read_range(args.spreadsheet_id, args.range, account=args.account)
        for row in rows:
            print(" | ".join(str(c) for c in row))
    else:
        parser.print_help()
