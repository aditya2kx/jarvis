#!/usr/bin/env python3
"""Rename and move files on Google Drive."""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import refresh_access_token

__all__ = ["rename_file", "move_file"]


def rename_file(token, file_id, new_name):
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    body = json.dumps({"name": new_name}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }, method="PATCH")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result


def move_file(token, file_id, old_parent_id, new_parent_id):
    url = (
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        f"?addParents={new_parent_id}&removeParents={old_parent_id}"
    )
    req = urllib.request.Request(url, data=b"{}", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }, method="PATCH")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result


def main():
    parser = argparse.ArgumentParser(description="Rename or move a file on Google Drive.")
    parser.add_argument("file_id", help="Google Drive file ID")
    parser.add_argument("--rename", dest="new_name", help="New file name")
    parser.add_argument("--move-from", dest="old_parent", help="Old parent folder ID")
    parser.add_argument("--move-to", dest="new_parent", help="New parent folder ID")
    args = parser.parse_args()

    token = refresh_access_token()

    if args.new_name:
        result = rename_file(token, args.file_id, args.new_name)
        print(f"Renamed -> {result['name']}")

    if args.old_parent and args.new_parent:
        result = move_file(token, args.file_id, args.old_parent, args.new_parent)
        print(f"Moved {result['name']} to new parent")


if __name__ == "__main__":
    main()
