#!/usr/bin/env python3
"""Recursively list all files in a Google Drive folder."""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import refresh_access_token, get_drive_id, project_dir

__all__ = ["list_folder", "walk_folder", "inventory_folder", "save_inventory"]


def list_folder(token, folder_id, page_token=None):
    q = f"'{folder_id}' in parents and trashed=false"
    params = {
        "q": q,
        "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
        "pageSize": 100,
        "orderBy": "name",
    }
    if page_token:
        params["pageToken"] = page_token
    url = f"https://www.googleapis.com/drive/v3/files?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def walk_folder(token, folder_id, path="", depth=0):
    result = list_folder(token, folder_id)
    files = result.get("files", [])

    while result.get("nextPageToken"):
        result = list_folder(token, folder_id, result["nextPageToken"])
        files.extend(result.get("files", []))

    all_items = []
    for f in files:
        is_folder = f["mimeType"] == "application/vnd.google-apps.folder"
        full_path = f"{path}/{f['name']}" if path else f['name']
        item = {
            "id": f["id"],
            "name": f["name"],
            "path": full_path,
            "mimeType": f["mimeType"],
            "size": f.get("size"),
            "modifiedTime": f.get("modifiedTime"),
            "isFolder": is_folder,
            "depth": depth,
        }
        all_items.append(item)

        if is_folder:
            children = walk_folder(token, f["id"], full_path, depth + 1)
            all_items.extend(children)

    return all_items


def inventory_folder(token, folder_id):
    """Return a recursive inventory for a Drive folder."""
    return walk_folder(token, folder_id)


def save_inventory(out_path, folder_id, items):
    """Write a Drive inventory JSON file to disk."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"folder_id": folder_id, "items": items}, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Recursively list all files in a Google Drive folder.",
    )
    parser.add_argument(
        "folder_id",
        nargs="?",
        help="Google Drive folder ID (defaults to config key taxes_root_id)",
    )
    parser.add_argument(
        "--folder-key",
        dest="folder_key",
        default=None,
        help="Config key for a folder ID, e.g. taxes_year_id",
    )
    parser.add_argument(
        "--json-out",
        dest="json_out",
        default=os.path.join(project_dir(), "extracted", "drive-2025-inventory.json"),
        help="Where to write the inventory JSON output",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Skip printing the tree; only write the JSON inventory",
    )
    args = parser.parse_args()

    if args.folder_key:
        folder_id = get_drive_id(args.folder_key)
        if not folder_id:
            print(f"Error: no folder ID for key {args.folder_key!r} in config.", file=sys.stderr)
            sys.exit(1)
    else:
        folder_id = args.folder_id if args.folder_id else get_drive_id("taxes_root_id")

    token = refresh_access_token()
    items = inventory_folder(token, folder_id)

    if not args.quiet:
        print("=== Drive folder inventory ===")
        for item in items:
            indent = "  " * item["depth"]
            icon = "[DIR]" if item["isFolder"] else "[FILE]"
            size = f" ({int(item['size']):,} bytes)" if item.get("size") else ""
            print(f"{indent}{icon} {item['name']}{size}  [{item['id']}]")
        print(f"\n=== Total: {len(items)} items ===")

    out_path = os.path.expanduser(args.json_out)
    save_inventory(out_path, folder_id, items)
    print(f"Saved inventory to {out_path}")


if __name__ == "__main__":
    main()
