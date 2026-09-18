"""Start/stop monitoring by replying to a pup-watch email.

The operator wanted to control this from his phone without curl and without an
admin token, so every tick checks the notification mailbox for a reply and acts
on it. Polling the mailbox we already own beats standing up an inbound-mail
path: no MX records, no webhook, no new secret — the same Gmail OAuth trio that
sends the sighting also reads the answer.

Three gates, all of which must pass, because "anyone who learns the address can
switch off the dog camera" is not an acceptable failure mode:

  1. the sender is one of PUPWATCH_NOTIFY_TO
  2. SPF **and** DKIM pass in Gmail's own Authentication-Results header
  3. the command is the first unquoted line, matched strictly

Idempotency comes from a Gmail label we own (`pupwatch-handled`), not from the
read/unread flag. That was the first design and it silently ignored every
command the operator sent: **Gmail marks a message you compose yourself as
already read**, so his replies never matched `is:unread` and were never even
considered. API-sent mail *does* arrive unread, which is why the live evidence
for PR #318 passed while the real thing did not work. A label we set ourselves
has no such hidden dependency on who sent the message or how.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

from . import notify, persist, sessions
from .config import Settings, notify_recipients

log = logging.getLogger("pup_watch")

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# The command must open the line. "stopped raining, he loved it" must not stop
# monitoring, so a bare keyword anywhere in the body is deliberately not enough.
_COMMAND_RE = re.compile(
    r"^\s*(?:pup[\s-]+)?(start|stop|status)\b\s*(\d+(?:\.\d+)?)?\s*(h|hr|hrs|hour|hours)?",
    re.IGNORECASE,
)
# Gmail's quoted-reply furniture, which we must skip to find what was typed.
_QUOTE_MARKERS = (
    re.compile(r"^\s*>"),
    re.compile(r"^\s*On .+ wrote:\s*$"),
    re.compile(r"^\s*-{2,}\s*Original Message", re.IGNORECASE),
    re.compile(r"^\s*From:\s", re.IGNORECASE),
)


@dataclass(frozen=True)
class Command:
    action: str
    hours: Optional[float]
    sender: str
    message_id: str
    subject: str
    received_ts: Optional[float]


def _headers(payload: dict) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}


def sender_address(raw: str) -> str:
    """'Someone <who@example.com>' -> 'who@example.com'. Lowercased to compare."""
    m = re.search(r"<([^>]+)>", raw or "")
    return (m.group(1) if m else (raw or "")).strip().strip("<>").lower()


def email_authenticated(auth_results: str) -> bool:
    """Require Gmail to have verified both SPF and DKIM for this message.

    Gmail writes this header itself on delivery, so a spoofed From cannot forge
    it — unlike the From header, which anyone can set.
    """
    text = (auth_results or "").lower()
    return "spf=pass" in text and "dkim=pass" in text


def _first_unquoted_line(body: str) -> str:
    for line in body.splitlines():
        if not line.strip():
            continue
        if any(m.search(line) for m in _QUOTE_MARKERS):
            break
        return line
    return ""


def parse_command(text: str) -> Optional[tuple[str, Optional[float]]]:
    m = _COMMAND_RE.match(text or "")
    if not m:
        return None
    action = m.group(1).lower()
    hours: Optional[float] = None
    if m.group(2):
        try:
            hours = float(m.group(2))
        except ValueError:
            hours = None
    return action, hours


def _decode_part(part: dict) -> str:
    data = (part.get("body") or {}).get("data") or ""
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 — a malformed part is just not a command
        return ""


def plain_text(payload: dict) -> str:
    """Best-effort text/plain extraction, walking multipart trees."""
    if payload.get("mimeType") == "text/plain":
        return _decode_part(payload)
    for part in payload.get("parts") or ():
        found = plain_text(part)
        if found:
            return found
    return ""


def _api(access: str, path: str, *, method: str = "GET", payload: Optional[dict] = None) -> dict:
    req = urllib.request.Request(
        f"{GMAIL_API}/{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        return json.loads(raw.decode()) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(f"gmail_{method.lower()} status={e.code} path={path} body={detail}") from e


HANDLED_LABEL = "pupwatch-handled"
# Markers stamped by other Jarvis systems that share this mailbox. Named rather
# than imported: those systems ship as separate containers, so pup-watch must not
# depend on their code. Keep in sync with tesla_aladdin_garage/notify.py.
FOREIGN_MARKER_HEADERS = ("x-jarvis-garage",)
_label_id_cache: dict[str, str] = {}


def handled_label_id(access: str) -> str:
    """Id of our bookkeeping label, creating it on first use.

    Hidden from both label and message lists: it is our state, not something the
    operator should see decorating his mail.
    """
    if HANDLED_LABEL in _label_id_cache:
        return _label_id_cache[HANDLED_LABEL]
    for label in (_api(access, "labels").get("labels") or ()):
        if label.get("name") == HANDLED_LABEL:
            _label_id_cache[HANDLED_LABEL] = label["id"]
            return label["id"]
    created = _api(access, "labels", method="POST", payload={
        "name": HANDLED_LABEL,
        "labelListVisibility": "labelHide",
        "messageListVisibility": "hide",
    })
    _label_id_cache[HANDLED_LABEL] = created["id"]
    return created["id"]


def _mark_handled(access: str, message_id: str, *, mark_read: bool) -> None:
    """Record that we have finished with this message.

    `mark_read` only for messages we actually acted on. Never for our own
    sighting mail: the unread badge IS the notification, and clearing it would
    hide the alert on the phone it was just sent to.
    """
    payload: dict[str, Any] = {"addLabelIds": [handled_label_id(access)]}
    if mark_read:
        payload["removeLabelIds"] = ["UNREAD"]
    _api(access, f"messages/{message_id}/modify", method="POST", payload=payload)


def find_commands(access: str, *, settings: Settings, now: float) -> list[Command]:
    """Unread inbox messages from allowlisted senders that carry a command."""
    allowed = {a.lower() for a in notify_recipients()}
    if not allowed:
        log.info("pup-watch skip reason=control_no_allowlist")
        return []

    # NOT `is:unread`: Gmail pre-reads mail the account sends itself, which is
    # precisely how the operator replies. Anything we have already looked at
    # carries our own label instead.
    listing = _api(access, "messages?" + urllib.parse.urlencode({
        "q": f"in:inbox newer_than:1d -label:{HANDLED_LABEL}",
        "maxResults": 10,
    }))
    out: list[Command] = []
    for stub in listing.get("messages") or ():
        msg = _api(access, f"messages/{stub['id']}?format=full")
        payload = msg.get("payload") or {}
        hdrs = _headers(payload)
        if any(hdrs.get(h) for h in FOREIGN_MARKER_HEADERS):
            # Another Jarvis system's notification, sharing this mailbox. It is
            # from an allowlisted sender (itself), so without this it would fall
            # through to the command parse — one unlucky subject line away from
            # switching monitoring off. Labelled, not read: bursts of garage mail
            # would otherwise fill the 10-message window and crowd out a real
            # command. Left unread so it still shows up as new mail for him.
            _mark_handled(access, stub["id"], mark_read=False)
            continue
        if hdrs.get(notify.MARKER_HEADER.lower()):
            # Mail we generated — it lands in INBOX because we are a recipient.
            # Deliberately NOT keyed on the SENT label: the operator sends from
            # this same mailbox, so his own replies are SENT+INBOX too.
            # Labelled (never marked read) so it drops out of the next query.
            _mark_handled(access, stub["id"], mark_read=False)
            continue
        sender = sender_address(hdrs.get("from", ""))
        subject = hdrs.get("subject", "")
        if sender not in allowed:
            continue
        # Gmail writes no Authentication-Results on mail an account sends to
        # itself, so the operator replying from the notification mailbox would
        # be refused by an SPF/DKIM-only gate (measured 2026-09-16). The SENT
        # label is the stronger signal anyway: only someone holding the account
        # can produce a sent message, whereas SPF/DKIM merely prove the From
        # domain. Third parties (the partner's account) still need SPF+DKIM.
        composed_by_this_mailbox = "SENT" in (msg.get("labelIds") or [])
        if (settings.control_require_email_auth
                and not composed_by_this_mailbox
                and not email_authenticated(hdrs.get("authentication-results", ""))):
            log.error("pup-watch fail reason=control_email_unauthenticated sender=%s id=%s",
                      sender, stub["id"])
            _mark_handled(access, stub["id"], mark_read=True)
            continue

        received: Optional[float] = None
        try:
            parsed_date = parsedate_to_datetime(hdrs.get("date", ""))
            if parsed_date.tzinfo is None:
                # "-0000" means "no zone stated"; .timestamp() would otherwise
                # assume local time and skew staleness by the UTC offset.
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            received = parsed_date.timestamp()
        except Exception:  # noqa: BLE001 — fall back to Gmail's internal date
            try:
                received = float(msg.get("internalDate", 0)) / 1000.0 or None
            except (TypeError, ValueError):
                received = None
        if received is not None and (now - received) > settings.control_max_age_minutes * 60:
            # A command found after an outage must not silently start monitoring
            # hours later; consume it so it cannot fire tomorrow either.
            log.info("pup-watch skip reason=control_command_stale sender=%s age_min=%.1f",
                     sender, (now - received) / 60.0)
            _mark_handled(access, stub["id"], mark_read=True)
            continue

        parsed = parse_command(_first_unquoted_line(plain_text(payload))) or parse_command(subject)
        if not parsed:
            # Ordinary mail from an allowlisted sender. Label it so we stop
            # re-reading it every minute, but leave it unread — it is his mail.
            _mark_handled(access, stub["id"], mark_read=False)
            continue
        action, hours = parsed
        out.append(Command(action=action, hours=hours, sender=sender,
                           message_id=stub["id"], subject=subject, received_ts=received))
    return out


def announce(summary: str, *, requested_by: str = "pup-watch itself") -> None:
    """Email a state change nobody asked for (e.g. an automatic stop).

    Never raises: an un-sendable notice must not break the poll that noticed it.
    """
    try:
        _confirm(notify.access_token(),
                 Command("status", None, requested_by, "", "", None), summary)
    except Exception as e:  # noqa: BLE001
        log.error("pup-watch fail reason=control_announce err=%r", e)


def _confirm(access: str, cmd: Command, summary: str) -> None:
    """Tell BOTH recipients the state changed — not just whoever typed it."""
    recipients = notify_recipients()
    if not recipients:
        return
    msg = notify.build_message(
        recipients,
        recipients[0],
        f"pup-watch: {summary}",
        "\n".join([
            summary,
            "",
            f"Requested by: {cmd.sender}",
            "",
            "Reply to any pup-watch email with:",
            "  start        — keep watching until you reply stop",
            "  start 4h     — watch for 4 hours, then stop on its own",
            "  stop         — stop monitoring",
            "  status       — what is happening right now",
        ]),
    )
    try:
        notify.gmail_send(access, msg)
    except Exception as e:  # noqa: BLE001 — a failed ack must not undo the action
        log.error("pup-watch fail reason=control_ack err=%r", e)


def apply(cmd: Command, *, settings: Settings, now: float) -> str:
    if cmd.action == "start":
        result = sessions.start(hours=cmd.hours, by=f"email:{cmd.sender}",
                                settings=settings, now=now)
        hrs = result["hours"]
        if hrs is None:
            return "monitoring started — it will keep watching until you reply stop"
        return f"monitoring started for {hrs:.2g}h (until {notify.local_time(now + hrs * 3600)})"
    if cmd.action == "stop":
        sessions.stop(by=f"email:{cmd.sender}", now=now)
        return "monitoring stopped"
    session = persist.load_session()
    state = persist.load_state()
    if not session.get("active"):
        return "monitoring is OFF"
    until = session.get("stop_after_ts")
    if until:
        tail = f", until {notify.local_time(float(until))}"
    else:
        tail = ", until you reply stop"
    last = state.get("last_notified_ts")
    seen = f"; last alert {notify.local_time(float(last))}" if last else "; no alert yet today"
    return f"monitoring is ON{tail}{seen}"


def poll_commands(*, settings: Settings, now: Optional[float] = None) -> list[dict[str, Any]]:
    """Consume any pending email commands. Never raises — a tick must still poll."""
    now = time.time() if now is None else now
    if not settings.control_email_enabled:
        return []
    try:
        access = notify.access_token()
    except Exception as e:  # noqa: BLE001
        log.error("pup-watch fail reason=control_no_gmail_token err=%r", e)
        return []

    applied: list[dict[str, Any]] = []
    try:
        commands = find_commands(access, settings=settings, now=now)
    except Exception as e:  # noqa: BLE001
        log.error("pup-watch fail reason=control_poll err=%r", e)
        return []

    for cmd in commands:
        try:
            summary = apply(cmd, settings=settings, now=now)
        except Exception as e:  # noqa: BLE001
            log.error("pup-watch fail reason=control_apply action=%s err=%r", cmd.action, e)
            continue
        # Consume before acknowledging: a failed ack is noise, but an unconsumed
        # command would re-fire on the next tick a minute later.
        try:
            _mark_handled(access, cmd.message_id, mark_read=True)
        except Exception as e:  # noqa: BLE001
            log.error("pup-watch fail reason=control_mark_handled err=%r", e)
        log.info("pup-watch control action=%s sender=%s result=%s", cmd.action, cmd.sender, summary)
        applied.append({"action": cmd.action, "sender": cmd.sender, "result": summary})
        try:
            _confirm(access, cmd, summary)
        except Exception as e:  # noqa: BLE001 — the command already took effect
            log.error("pup-watch fail reason=control_ack err=%r", e)
    return applied
