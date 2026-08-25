"""Gmail notify for garage geofence events. Fail-open: log and continue.

Cloud Run mounts GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN
for **aditya.2ky@gmail.com** (never Palmetto / store Gmail).
To and From default to that address (GARAGE_NOTIFY_TO).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from typing import Any, Optional

from cloud.tesla_aladdin_garage.month_cost import (
    format_cursor_cost_lines,
    format_cursor_cost_subject,
    month_cursor_cost,
)

log = logging.getLogger("tesla_aladdin_garage")

DEFAULT_TO = "aditya.2ky@gmail.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

_SUBJECTS = {
    "opened": "Big Peach opened",
    "skip_already_open": "Big Peach already open — no command",
    "open_error": "Big Peach open FAILED",
}


def _fmt_m(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    try:
        return f"{float(value):.0f} m"
    except (TypeError, ValueError):
        return str(value)


def email_subject(event: str, fields: dict[str, Any]) -> str:
    """Subject always includes Tesla metres-from-home at trigger."""
    head = _SUBJECTS.get(event, f"Dhanno garage: {event}")
    tesla = _fmt_m(fields.get("distance_m"))
    enter = _fmt_m(fields.get("enter_m"))
    extra = " simulated" if fields.get("simulated") else ""
    cost = fields.get("cursor_cost_subject") or ""
    cost_bit = f" — {cost}" if cost else ""
    return f"{head} — Tesla {tesla} from home (enter {enter}){extra}{cost_bit}"


def email_body(event: str, fields: dict[str, Any]) -> str:
    tesla = _fmt_m(fields.get("distance_m"))
    enter = _fmt_m(fields.get("enter_m"))
    sim = " (simulated enter; Tesla metres are last live poll if any)" if fields.get("simulated") else ""
    cost_lines = fields.get("cursor_cost_lines") or []
    return "\n".join(
        [
            f"Tesla distance from home when this fired: {tesla}{sim}",
            f"Geofence enter radius: {enter}",
            "",
            *cost_lines,
            "",
            f"Event: {event}",
            f"Door: {fields.get('door', 'Big Peach')}",
            f"Door status: {fields.get('door_status')}",
            f"VIN: {fields.get('vin', '')}",
            f"Detail: {fields.get('detail', '')}",
            "",
            "If this fired too early or too late, POST /config {\"enter_m\": N} (admin token).",
            "Already-open means someone used the wall button / app — no Aladdin command was sent.",
        ]
    )


def send_garage_email(event: str, fields: dict[str, Any], *, to: Optional[str] = None) -> bool:
    """Send one notify email. Returns False if skipped or send failed."""
    dest = (to or os.environ.get("GARAGE_NOTIFY_TO") or DEFAULT_TO).strip()
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    if not dest or not client_id or not client_secret or not refresh:
        log.info("tesla-aladdin-garage skip reason=notify_unconfigured event=%s", event)
        return False
    payload = dict(fields)
    if "cursor_cost_lines" not in payload:
        info = month_cursor_cost()
        payload["cursor_cost_lines"] = format_cursor_cost_lines(info)
        payload["cursor_cost_subject"] = format_cursor_cost_subject(info)
    subject = email_subject(event, payload)
    lines = email_body(event, payload)
    try:
        access = _access_token(client_id, client_secret, refresh)
        _gmail_send(access, dest, dest, subject, lines)
        log.info("tesla-aladdin-garage notify event=%s to_len=%s", event, len(dest))
        return True
    except Exception as e:
        log.error("tesla-aladdin-garage fail reason=notify err=%s", e)
        return False


def _access_token(client_id: str, client_secret: str, refresh: str) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }
    ).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    token = data.get("access_token") or ""
    if not token:
        raise RuntimeError("gmail_refresh_no_access_token")
    return token


def _gmail_send(access: str, to: str, from_addr: str, subject: str, text: str) -> None:
    msg = MIMEText(text)
    msg["to"] = to
    msg["from"] = from_addr
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = json.dumps({"raw": raw}).encode()
    req = urllib.request.Request(
        GMAIL_SEND,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"gmail_send status={e.code} body={err}") from e
