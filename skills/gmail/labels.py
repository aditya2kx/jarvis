#!/usr/bin/env python3
"""Manage Gmail labels — list, create, apply, remove."""

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from skills.gmail.auth import get_gmail_token

__all__ = ["list_labels", "create_label", "apply_labels", "remove_labels"]

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _api_get(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _api_post(url, token, data, method="POST"):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def list_labels(account="palmetto"):
    """List all labels in the account."""
    token = get_gmail_token(account)
    data = _api_get(f"{GMAIL_API}/labels", token)
    return data.get("labels", [])


def create_label(name, account="palmetto"):
    """Create a new label. Returns the created label object."""
    token = get_gmail_token(account)
    result = _api_post(f"{GMAIL_API}/labels", token, {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    })
    print(f"  Created label: {result['name']} [{result['id']}]")
    return result


def apply_labels(message_id, label_ids, account="palmetto"):
    """Add labels to a message."""
    token = get_gmail_token(account)
    return _api_post(f"{GMAIL_API}/messages/{message_id}/modify", token, {
        "addLabelIds": label_ids,
    })


def remove_labels(message_id, label_ids, account="palmetto"):
    """Remove labels from a message."""
    token = get_gmail_token(account)
    return _api_post(f"{GMAIL_API}/messages/{message_id}/modify", token, {
        "removeLabelIds": label_ids,
    })


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gmail label operations")
    parser.add_argument("action", choices=["list", "create"])
    parser.add_argument("--name", help="Label name (for create)")
    parser.add_argument("--account", default="palmetto")
    args = parser.parse_args()

    if args.action == "list":
        labels = list_labels(account=args.account)
        for l in sorted(labels, key=lambda x: x["name"]):
            print(f"  {l['name']} [{l['id']}]")
    elif args.action == "create":
        if not args.name:
            parser.error("--name required for create")
        create_label(args.name, account=args.account)
