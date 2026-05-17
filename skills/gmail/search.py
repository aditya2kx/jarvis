#!/usr/bin/env python3
"""Search Gmail messages using the Gmail API."""

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from skills.gmail.auth import get_gmail_token

__all__ = ["search_messages", "list_messages"]

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _api_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def search_messages(query, account="palmetto", max_results=20):
    """Search Gmail with a query string (same syntax as Gmail search bar).

    Returns a list of message summaries with id, threadId, snippet, subject, from, date.
    """
    token = get_gmail_token(account)
    params = urllib.parse.urlencode({"q": query, "maxResults": max_results})
    url = f"{GMAIL_API}/messages?{params}"
    data = _api_get(url, token)

    messages = data.get("messages", [])
    results = []
    for msg_stub in messages:
        msg = _api_get(f"{GMAIL_API}/messages/{msg_stub['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date", token)
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        results.append({
            "id": msg["id"],
            "threadId": msg["threadId"],
            "snippet": msg.get("snippet", ""),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "labelIds": msg.get("labelIds", []),
        })

    return results


def list_messages(label="INBOX", account="palmetto", max_results=20):
    """List messages in a specific label (default: INBOX)."""
    token = get_gmail_token(account)
    params = urllib.parse.urlencode({"labelIds": label, "maxResults": max_results})
    url = f"{GMAIL_API}/messages?{params}"
    data = _api_get(url, token)

    messages = data.get("messages", [])
    results = []
    for msg_stub in messages:
        msg = _api_get(f"{GMAIL_API}/messages/{msg_stub['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date", token)
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        results.append({
            "id": msg["id"],
            "threadId": msg["threadId"],
            "snippet": msg.get("snippet", ""),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "labelIds": msg.get("labelIds", []),
        })

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Search Gmail")
    parser.add_argument("query", help="Gmail search query")
    parser.add_argument("--account", default="palmetto")
    parser.add_argument("--max", type=int, default=10)
    args = parser.parse_args()

    results = search_messages(args.query, account=args.account, max_results=args.max)
    for r in results:
        print(f"[{r['date']}] {r['from']}")
        print(f"  Subject: {r['subject']}")
        print(f"  Snippet: {r['snippet'][:100]}")
        print()
