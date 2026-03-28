#!/usr/bin/env python3
"""Create a Google Drive folder using the direct Google API helper path."""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from core.config_loader import refresh_access_token, get_drive_id

__all__ = ["create_folder"]


def create_folder(token, name, parent_id):
    body = json.dumps(
        {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
    ).encode()
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/files",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser(description="Create a Google Drive folder.")
    parser.add_argument("name", help="Folder name to create")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--parent-id", dest="parent_id", help="Parent Google Drive folder ID")
    group.add_argument(
        "--parent-key",
        dest="parent_key",
        help="Config key for parent folder ID, e.g. taxes_root_id",
    )
    args = parser.parse_args()

    if args.parent_id:
        parent_id = args.parent_id
    else:
        parent_id = get_drive_id(args.parent_key)
        if not parent_id:
            print(
                f"Error: no folder ID for key {args.parent_key!r} in config.",
                file=sys.stderr,
            )
            sys.exit(1)

    token = refresh_access_token()
    result = create_folder(token, args.name, parent_id)
    print(f"Created folder: {result['name']}  [{result['id']}]")


if __name__ == "__main__":
    main()
