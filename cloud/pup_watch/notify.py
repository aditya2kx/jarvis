"""Email the sighting to everyone on the list, with the frame that fired.

Same Gmail OAuth path as cloud/tesla_aladdin_garage/notify.py (Cloud Run mounts
GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN), with two
differences: multiple recipients, and the annotated frame attached so the email
shows the sighting instead of just asserting it.

Recipients come from PUPWATCH_NOTIFY_TO, not from source — they are personal
addresses and the repo keeps PII out of git.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from .config import notify_recipients

log = logging.getLogger("pup_watch")

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
# The whole system is anchored to the store's timezone elsewhere in the repo;
# keep sighting times readable in the same one.
LOCAL_TZ = ZoneInfo("America/Chicago")


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LOCAL_TZ).strftime("%-I:%M:%S %p %Z")


def subject(fields: dict[str, Any]) -> str:
    conf = fields.get("identity_confidence")
    tail = f" (match {float(conf):.0%})" if isinstance(conf, (int, float)) and conf else ""
    yard = fields.get("camera_label") or fields.get("camera") or "yard"
    return f"Pup is out in the {yard}{tail}"


def body(fields: dict[str, Any]) -> str:
    seen_ts = fields.get("seen_ts")
    lines = [
        f"Spotted at: {_local(float(seen_ts))}" if seen_ts else "Spotted just now",
        f"Camera: {fields.get('camera_label') or fields.get('camera', '')}",
        "",
        f"Dogs in yard: {fields.get('dogs', '?')} (he is only ever let out alone)",
        f"People in yard: {fields.get('persons', '?')}",
        f"Frames agreeing this poll: {fields.get('hits', '?')} of {fields.get('frames', '?')}",
    ]
    if fields.get("dog_box_px"):
        lines.append(f"His height in frame: {fields['dog_box_px']} px")
    if fields.get("cream_fraction") is not None:
        lines.append(f"Cream-coat match: {float(fields['cream_fraction']):.0%} of the box")
    ident = fields.get("identity_notes")
    if fields.get("identity_confidence") is not None:
        lines += [
            "",
            f"Identity check: {float(fields['identity_confidence']):.0%} confident it is him"
            + (f" — {ident}" if ident else ""),
        ]
    elif fields.get("identity_skipped"):
        lines += ["", f"Identity check skipped: {fields['identity_skipped']}"]
    lines += [
        "",
        "The attached frame shows the detection boxes that triggered this email.",
        "You will not get another email for this visit until he has been out of",
        "sight for a while, so this is one email per outing rather than per minute.",
        "",
        "Stop monitoring:  POST /session/stop  (see cloud/pup_watch/README.md)",
    ]
    return "\n".join(lines)


def _access_token(client_id: str, client_secret: str, refresh: str) -> str:
    payload = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    token = data.get("access_token") or ""
    if not token:
        raise RuntimeError("gmail_refresh_no_access_token")
    return token


def build_message(
    recipients: Sequence[str],
    sender: str,
    subject_line: str,
    text: str,
    image: Optional[bytes] = None,
) -> MIMEMultipart:
    msg = MIMEMultipart()
    # RFC 5322 allows a comma-separated address list in a single To header,
    # which is all "send to both of us" needs.
    msg["to"] = ", ".join(recipients)
    msg["from"] = sender
    msg["subject"] = subject_line
    msg.attach(MIMEText(text))
    if image:
        part = MIMEImage(image, _subtype="jpeg")
        part.add_header("Content-Disposition", "attachment", filename="sighting.jpg")
        msg.attach(part)
    return msg


def _gmail_send(access: str, msg: MIMEMultipart) -> None:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    req = urllib.request.Request(
        GMAIL_SEND,
        data=json.dumps({"raw": raw}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"gmail_send status={e.code} body={detail}") from e


def send_sighting(fields: dict[str, Any], image: Optional[bytes] = None) -> bool:
    """Send one sighting email. Returns False if skipped or failed (never raises)."""
    recipients = notify_recipients()
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    if not recipients or not client_id or not client_secret or not refresh:
        log.info(
            "pup-watch skip reason=notify_unconfigured recipients=%d creds=%s",
            len(recipients), bool(client_id and client_secret and refresh),
        )
        return False
    sender = os.environ.get("PUPWATCH_NOTIFY_FROM", "").strip() or recipients[0]
    try:
        access = _access_token(client_id, client_secret, refresh)
        _gmail_send(access, build_message(recipients, sender, subject(fields), body(fields), image))
        log.info("pup-watch notify sent recipients=%d camera=%s", len(recipients), fields.get("camera"))
        return True
    except Exception as e:  # noqa: BLE001 — a failed email must not kill the poll
        log.error("pup-watch fail reason=notify err=%r", e)
        return False
