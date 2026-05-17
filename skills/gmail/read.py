#!/usr/bin/env python3
"""Read full Gmail message content."""

import base64
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from skills.gmail.auth import get_gmail_token

__all__ = ["read_message", "get_message_body"]

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _api_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def read_message(message_id, account="palmetto"):
    """Read a full message by ID. Returns parsed headers and body text."""
    token = get_gmail_token(account)
    msg = _api_get(f"{GMAIL_API}/messages/{message_id}?format=full", token)

    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    body = get_message_body(msg.get("payload", {}))

    return {
        "id": msg["id"],
        "threadId": msg["threadId"],
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "date": headers.get("Date", ""),
        "body": body,
        "labelIds": msg.get("labelIds", []),
        "snippet": msg.get("snippet", ""),
    }


def get_message_body(payload):
    """Extract plain-text body from a Gmail message payload, traversing MIME parts."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    for part in parts:
        if part.get("mimeType", "").startswith("multipart/"):
            result = get_message_body(part)
            if result:
                return result

    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    return ""


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read a Gmail message")
    parser.add_argument("message_id", help="Gmail message ID")
    parser.add_argument("--account", default="palmetto")
    args = parser.parse_args()

    msg = read_message(args.message_id, account=args.account)
    print(f"From: {msg['from']}")
    print(f"To: {msg['to']}")
    print(f"Date: {msg['date']}")
    print(f"Subject: {msg['subject']}")
    print(f"\n{msg['body'][:2000]}")
