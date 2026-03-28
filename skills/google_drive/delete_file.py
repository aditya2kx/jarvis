#!/usr/bin/env python3
"""Delete a file on Google Drive by ID (same OAuth as other scripts)."""

import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import refresh_access_token


def delete_file(token, file_id):
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
    with urllib.request.urlopen(req) as resp:
        return resp.status


def main():
    if len(sys.argv) != 2:
        print("Usage: delete_drive_file.py <file_id>", file=sys.stderr)
        sys.exit(1)
    file_id = sys.argv[1].strip()
    token = refresh_access_token()
    delete_file(token, file_id)
    print(f"Deleted Drive file {file_id}")


if __name__ == "__main__":
    main()
