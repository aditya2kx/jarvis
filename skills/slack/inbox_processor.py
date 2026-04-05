#!/usr/bin/env python3
"""Long-running inbox processor — polls queued Slack messages and acts on them.

Bridges the gap between the Socket Mode listener (instant message receipt) and
the AI agent (only active during Cursor turns). Runs for hours in the background,
checking every N seconds for unread messages.

For each unread message it:
  1. Acknowledges on Slack with a meaningful reply (not just "queued")
  2. Writes to /tmp/jarvis-pending-actions.json for the AI to pick up
  3. Handles simple patterns itself (decisions, confirmations)

Start:
    python skills/slack/inbox_processor.py            # 4 hours, 2-min polls
    python skills/slack/inbox_processor.py --hours 8  # 8 hours
    python skills/slack/inbox_processor.py --interval 60  # every 60s

The AI agent reads /tmp/jarvis-pending-actions.json at the start of every turn.
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time

os.environ["PYTHONUNBUFFERED"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

INBOX_FILE = pathlib.Path("/tmp/jarvis-slack-inbox.json")
PENDING_FILE = pathlib.Path("/tmp/jarvis-pending-actions.json")
PID_FILE = pathlib.Path("/tmp/jarvis-inbox-processor.pid")

# Patterns the processor can handle without AI
DECISION_PATTERN = re.compile(
    r"^(?:do\s+)?(\d+)\.?\s*$", re.IGNORECASE
)
YES_NO_PATTERN = re.compile(
    r"^(yes|no|yep|nope|yeah|nah|sure|ok|okay|y|n)\b", re.IGNORECASE
)
SKIP_PATTERN = re.compile(
    r"^skip\s+(.+)", re.IGNORECASE
)


def _load_json(path):
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def _slack_reply(text):
    """Send a DM reply via Slack."""
    try:
        from skills.slack.adapter import send_progress
        send_progress(text)
    except Exception as e:
        print(f"[processor] Reply failed: {e}")


def _classify_message(text):
    """Classify a message into an action type.

    Returns (action_type, parsed_value) tuple:
      - ("decision", "1")       — user picked option 1
      - ("yes_no", "yes")       — confirmation
      - ("skip", "schwab")      — skip a portal
      - ("instruction", text)   — general instruction for AI
    """
    text = text.strip()

    if text.startswith("__CMD_"):
        return ("internal", text)

    m = DECISION_PATTERN.match(text)
    if m:
        return ("decision", m.group(1))

    m = YES_NO_PATTERN.match(text)
    if m:
        return ("yes_no", m.group(1).lower())

    m = SKIP_PATTERN.match(text)
    if m:
        return ("skip", m.group(1).strip())

    return ("instruction", text)


def _queue_pending(action_type, value, original_text, ts):
    """Write an action to the pending-actions file for the AI."""
    pending = _load_json(PENDING_FILE)
    pending.append({
        "type": action_type,
        "value": value,
        "original_text": original_text,
        "ts": ts,
        "queued_at": time.time(),
        "processed_by_ai": False,
    })
    if len(pending) > 50:
        pending = pending[-50:]
    _save_json(PENDING_FILE, pending)


def process_once():
    """Check inbox, process unread messages, return count processed."""
    inbox = _load_json(INBOX_FILE)
    unread = [m for m in inbox if not m.get("read")]

    if not unread:
        return 0

    count = 0
    for msg in unread:
        text = msg.get("text", "")
        ts = msg.get("ts", "")
        msg["read"] = True

        if text.startswith("__CMD_"):
            _queue_pending("internal", text, text, ts)
            continue

        action_type, value = _classify_message(text)

        _queue_pending(action_type, value, text, ts)
        count += 1

        if action_type == "decision":
            _slack_reply(
                f":white_check_mark: Noted your choice: *option {value}*. "
                f"Will execute on next action cycle."
            )
        elif action_type == "yes_no":
            _slack_reply(
                f":white_check_mark: Got your answer: *{value}*. "
                f"Will proceed accordingly."
            )
        elif action_type == "skip":
            _slack_reply(
                f":fast_forward: Will skip *{value}*. Noted."
            )
        elif action_type == "instruction":
            _slack_reply(
                f":memo: Received your instruction. "
                f"Will act on it in my next cycle:\n> _{text[:200]}_"
            )

    _save_json(INBOX_FILE, inbox)
    return count


def read_pending(mark_processed=True):
    """Read unprocessed pending actions. Called by the AI agent.

    Returns list of action dicts. Marks them as processed if requested.
    """
    pending = _load_json(PENDING_FILE)
    unprocessed = [p for p in pending if not p.get("processed_by_ai")]

    if mark_processed and unprocessed:
        for p in pending:
            if not p.get("processed_by_ai"):
                p["processed_by_ai"] = True
        _save_json(PENDING_FILE, pending)

    return unprocessed


def clear_pending():
    """Clear all pending actions (called after AI processes them all)."""
    _save_json(PENDING_FILE, [])


def main():
    parser = argparse.ArgumentParser(description="Jarvis Slack inbox processor")
    parser.add_argument("--hours", type=float, default=4,
                        help="How many hours to run (default: 4)")
    parser.add_argument("--interval", type=int, default=120,
                        help="Poll interval in seconds (default: 120)")
    args = parser.parse_args()

    max_runtime = args.hours * 3600
    interval = args.interval

    PID_FILE.write_text(str(os.getpid()))

    print(f"[processor] Starting inbox processor (PID {os.getpid()})")
    print(f"[processor] Runtime: {args.hours}h, interval: {interval}s")
    print(f"[processor] Pending actions: {PENDING_FILE}")

    _slack_reply(
        f":robot_face: Inbox processor started. "
        f"I'll check for your messages every {interval // 60} min for the next "
        f"{args.hours:.0f} hours. Send me anything — I'll pick it up."
    )

    start = time.time()
    cycles = 0

    try:
        while time.time() - start < max_runtime:
            count = process_once()
            cycles += 1
            if count:
                print(f"[processor] Cycle {cycles}: processed {count} messages")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[processor] Interrupted.")
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink()
        elapsed_h = (time.time() - start) / 3600
        print(f"[processor] Stopped after {elapsed_h:.1f}h, {cycles} cycles")
        _slack_reply(":zzz: Inbox processor stopped. Messages will queue until next session.")


if __name__ == "__main__":
    main()
