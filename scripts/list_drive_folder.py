#!/usr/bin/env python3
"""Recursively list all files in a Google Drive folder."""

import json
import os
import sys
import urllib.request
import urllib.parse

from config_loader import refresh_access_token, get_drive_id, project_dir


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


def main():
    folder_id = sys.argv[1] if len(sys.argv) > 1 else get_drive_id("taxes_root_id")

    token = refresh_access_token()

    # First, show the top-level Taxes folder contents
    print("=== Taxes folder (top level) ===")
    items = walk_folder(token, folder_id)

    for item in items:
        indent = "  " * item["depth"]
        icon = "📁" if item["isFolder"] else "📄"
        size = f" ({int(item['size']):,} bytes)" if item.get("size") else ""
        print(f"{indent}{icon} {item['name']}{size}  [{item['id']}]")

    print(f"\n=== Total: {len(items)} items ===")

    # Output as JSON for programmatic use
    out_path = os.path.join(project_dir(), "extracted", "drive-2025-inventory.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"folder_id": folder_id, "items": items}, f, indent=2)
    print(f"Saved inventory to extracted/drive-2025-inventory.json")


if __name__ == "__main__":
    main()
