#!/usr/bin/env python3
"""Upload local files to a specific Google Drive folder."""

import argparse
import json
import os
import sys
import urllib.request
import mimetypes

from config_loader import refresh_access_token, get_drive_id

__all__ = ["upload_file"]


def upload_file(token, local_path, folder_id, drive_name=None):
    if drive_name is None:
        drive_name = os.path.basename(local_path)

    mime_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    metadata = json.dumps({
        "name": drive_name,
        "parents": [folder_id],
    }).encode()

    with open(local_path, "rb") as f:
        file_data = f.read()

    boundary = b"----MultipartBoundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Type: application/json; charset=UTF-8\r\n\r\n'
        + metadata + b"\r\n"
        b"--" + boundary + b"\r\n"
        b"Content-Type: " + mime_type.encode() + b"\r\n\r\n"
        + file_data + b"\r\n"
        b"--" + boundary + b"--"
    )

    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/related; boundary={boundary.decode()}",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    print(f"  Uploaded: {drive_name}  [{result['id']}]")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Upload a local file to a Google Drive folder.",
    )
    parser.add_argument("local_path", help="Path to the file to upload")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--folder-id",
        dest="folder_id",
        help="Destination Google Drive folder ID",
    )
    group.add_argument(
        "--folder-key",
        dest="folder_key",
        help="Config key for folder ID (passed to get_drive_id), e.g. taxes_year_id",
    )
    parser.add_argument(
        "--name",
        dest="name",
        default=None,
        help="Name for the file on Drive (default: basename of local file)",
    )
    args = parser.parse_args()

    local_path = os.path.expanduser(args.local_path)
    if not os.path.isfile(local_path):
        print(f"Error: not a file: {local_path}", file=sys.stderr)
        sys.exit(1)

    if args.folder_id:
        folder_id = args.folder_id
    else:
        folder_id = get_drive_id(args.folder_key)
        if not folder_id:
            print(
                f"Error: no folder ID for key {args.folder_key!r} in config.",
                file=sys.stderr,
            )
            sys.exit(1)

    token = refresh_access_token()
    upload_file(token, local_path, folder_id, args.name)


if __name__ == "__main__":
    main()
