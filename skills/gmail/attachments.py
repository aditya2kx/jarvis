#!/usr/bin/env python3
"""Download attachments from Gmail messages."""

import base64
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from skills.gmail.auth import get_gmail_token

__all__ = ["list_attachments", "download_attachment"]

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _api_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def list_attachments(message_id, account="palmetto"):
    """List all attachments in a message. Returns list of {filename, mimeType, size, attachmentId}."""
    token = get_gmail_token(account)
    msg = _api_get(f"{GMAIL_API}/messages/{message_id}?format=full", token)

    attachments = []
    _walk_parts(msg.get("payload", {}), attachments)
    return attachments


def _walk_parts(payload, results):
    """Recursively walk MIME parts to find attachments."""
    if payload.get("filename") and payload.get("body", {}).get("attachmentId"):
        results.append({
            "filename": payload["filename"],
            "mimeType": payload.get("mimeType", "application/octet-stream"),
            "size": payload.get("body", {}).get("size", 0),
            "attachmentId": payload["body"]["attachmentId"],
        })

    for part in payload.get("parts", []):
        _walk_parts(part, results)


def download_attachment(message_id, attachment_id, save_path, account="palmetto"):
    """Download an attachment and save it to disk."""
    token = get_gmail_token(account)
    url = f"{GMAIL_API}/messages/{message_id}/attachments/{attachment_id}"
    data = _api_get(url, token)

    file_data = base64.urlsafe_b64decode(data["data"])
    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(file_data)

    print(f"  Saved: {save_path} ({len(file_data)} bytes)")
    return save_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download Gmail attachments")
    parser.add_argument("message_id", help="Gmail message ID")
    parser.add_argument("--account", default="palmetto")
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args()

    attachments = list_attachments(args.message_id, account=args.account)
    if not attachments:
        print("No attachments found.")
    else:
        for att in attachments:
            path = os.path.join(args.output_dir, att["filename"])
            download_attachment(args.message_id, att["attachmentId"], path, account=args.account)
