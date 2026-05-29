#!/usr/bin/env python3
"""BHAGA Slack command polling fallback (belt-and-suspenders).

Run by a lightweight launchd plist every 15 minutes, 24/7.  Makes a single
conversations.history API call to the BHAGA DM channel, checks for unhandled
"retry" / "refresh <date>" / "status" commands, and triggers recovery if
needed.  Works even if the Socket Mode listener is dead — the operator's
"retry" message is picked up within 15 min worst case.

Idempotency: tracks the last processed command timestamp in
~/.bhaga/state/last_command_ts.txt so the same message is never processed
twice.

Usage:
    python3 -m skills.slack.poll_commands            # run once and exit
    python3 skills/slack/poll_commands.py             # same
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from skills.slack.command_handler import (
    AGENT_NAME,
    DM_CHANNEL,
    handle_command,
    handle_ready,
    has_pending_otp,
    is_command,
)

STATE_DIR = pathlib.Path.home() / ".bhaga" / "state"
LAST_TS_FILE = STATE_DIR / "last_command_ts.txt"


def _read_last_ts() -> str:
    """Read the last processed command timestamp. Returns '0' if unset."""
    if LAST_TS_FILE.exists():
        try:
            return LAST_TS_FILE.read_text().strip()
        except OSError:
            pass
    return "0"


def _write_last_ts(ts: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LAST_TS_FILE.write_text(ts + "\n")


def _send_dm(text: str) -> None:
    try:
        from skills.slack.adapter import send_message
        send_message(DM_CHANNEL, text, agent=AGENT_NAME)
    except Exception as exc:
        print(f"[poll_commands] DM send failed: {exc}", file=sys.stderr)


def poll_once() -> int:
    """Check the BHAGA DM channel for unhandled commands. Returns 0 on success."""
    from skills.slack.adapter import _api_call

    last_ts = _read_last_ts()

    result = _api_call(
        "conversations.history",
        params={"channel": DM_CHANNEL, "limit": "10", "oldest": last_ts},
        agent=AGENT_NAME,
    )
    if not result.get("ok"):
        print(f"[poll_commands] conversations.history failed: {result.get('error')}")
        return 1

    messages = result.get("messages", [])
    if not messages:
        return 0

    # Sort oldest-first so we process in chronological order
    messages.sort(key=lambda m: float(m.get("ts", "0")))

    newest_ts = last_ts
    commands_handled = 0

    for msg in messages:
        ts = msg.get("ts", "0")
        text = msg.get("text", "")
        user_id = msg.get("user", "")

        # Skip bot messages
        if msg.get("bot_id") or msg.get("subtype"):
            if float(ts) > float(newest_ts):
                newest_ts = ts
            continue

        # Skip if not newer than last processed
        if float(ts) <= float(last_ts):
            continue

        # Track the newest timestamp regardless of whether it's a command
        if float(ts) > float(newest_ts):
            newest_ts = ts

        # READY-handshake resume (two-step OTP availability). When the daily
        # run posted a READY request and exited (laptop was closed), the
        # operator's READY reply may have arrived while the live listener was
        # down — so we detect it here from the DM backlog and resume the
        # checkpointed run. Checked BEFORE is_command so "go"/"ok" route here.
        ready_ack = handle_ready(text)
        if ready_ack:
            _send_dm(ready_ack)
            commands_handled += 1
            continue

        if not is_command(text):
            continue

        # Don't process commands while an OTP is pending
        if has_pending_otp():
            print(f"[poll_commands] Skipping command '{text}' — OTP pending")
            continue

        print(f"[poll_commands] Processing command: '{text}' from {user_id}")
        response = handle_command(text, user_id)
        if response:
            _send_dm(response)
            commands_handled += 1

    # Always advance the cursor to the newest message we've seen
    if float(newest_ts) > float(last_ts):
        _write_last_ts(newest_ts)

    if commands_handled:
        print(f"[poll_commands] Handled {commands_handled} command(s)")

    return 0


def main() -> int:
    try:
        return poll_once()
    except Exception as exc:
        print(f"[poll_commands] Fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
