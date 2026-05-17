#!/usr/bin/env python3
"""Send emails via the Gmail API."""

import base64
import json
import os
import sys
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from skills.gmail.auth import get_gmail_token

__all__ = ["send_message", "reply_to_message"]

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _api_post(url, token, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def send_message(to, subject, body_text, account="palmetto", attachments=None):
    """Send a new email. Returns the sent message metadata."""
    token = get_gmail_token(account)

    if attachments:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body_text, "plain"))
        for filepath in attachments:
            part = MIMEBase("application", "octet-stream")
            with open(filepath, "rb") as f:
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(filepath)}")
            msg.attach(part)
    else:
        msg = MIMEText(body_text)

    msg["to"] = to
    msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = _api_post(f"{GMAIL_API}/messages/send", token, {"raw": raw})
    print(f"  Sent message: {result['id']}")
    return result


def reply_to_message(message_id, thread_id, body_text, account="palmetto"):
    """Reply to an existing message in the same thread."""
    token = get_gmail_token(account)

    # Fetch original to get headers
    req = urllib.request.Request(
        f"{GMAIL_API}/messages/{message_id}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=To&metadataHeaders=Message-ID",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        orig = json.loads(resp.read())

    headers = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}
    reply_to = headers.get("From", "")
    subject = headers.get("Subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = MIMEText(body_text)
    msg["to"] = reply_to
    msg["subject"] = subject
    msg["In-Reply-To"] = headers.get("Message-ID", "")
    msg["References"] = headers.get("Message-ID", "")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = _api_post(f"{GMAIL_API}/messages/send", token, {"raw": raw, "threadId": thread_id})
    print(f"  Replied in thread: {result['id']}")
    return result
