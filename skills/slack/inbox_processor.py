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

INBOX_FILE = pathlib.Path("/tmp/jarvis-slack-inbox.json")  # legacy / default
PENDING_FILE = pathlib.Path("/tmp/jarvis-pending-actions.json")
PID_FILE = pathlib.Path("/tmp/jarvis-inbox-processor.pid")


def _all_inbox_files():
    """Return every inbox file path: the legacy default + every per-agent file."""
    import glob
    paths = [INBOX_FILE] if INBOX_FILE.exists() else []
    for p in glob.glob("/tmp/jarvis-slack-inbox-*.json"):
        paths.append(pathlib.Path(p))
    return paths


def _agent_for_inbox(path):
    """Derive agent name from an inbox file path. Returns None for legacy default."""
    name = path.name
    if name == "jarvis-slack-inbox.json":
        return None
    if name.startswith("jarvis-slack-inbox-") and name.endswith(".json"):
        return name[len("jarvis-slack-inbox-"):-len(".json")]
    return None

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


def _slack_reply(text, agent=None):
    """Send a DM reply via Slack as the specified agent's bot.

    agent=None routes through the default (CHITRA) bot for backward compat.
    agent="bhaga" routes through BHAGA's bot using the BHAGA DM channel.
    """
    try:
        from skills.slack.adapter import send_progress
        send_progress(text, agent=agent)
    except Exception as e:
        print(f"[processor] Reply failed (agent={agent}): {e}")


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


def _queue_pending(action_type, value, original_text, ts, agent=None):
    """Write an action to the pending-actions file for the AI."""
    pending = _load_json(PENDING_FILE)
    pending.append({
        "type": action_type,
        "value": value,
        "original_text": original_text,
        "agent": agent,  # which agent's DM the message came from (None = default/CHITRA)
        "ts": ts,
        "queued_at": time.time(),
        "processed_by_ai": False,
    })
    if len(pending) > 50:
        pending = pending[-50:]
    _save_json(PENDING_FILE, pending)


def _process_inbox_file(path):
    """Process unread messages from one inbox file. Returns count processed."""
    agent = _agent_for_inbox(path)
    inbox = _load_json(path)
    unread = [m for m in inbox if not m.get("read")]

    if not unread:
        return 0

    count = 0
    for msg in unread:
        text = msg.get("text", "")
        ts = msg.get("ts", "")
        msg["read"] = True

        if text.startswith("__CMD_"):
            _queue_pending("internal", text, text, ts, agent=agent)
            continue

        action_type, value = _classify_message(text)
        _queue_pending(action_type, value, text, ts, agent=agent)
        count += 1

        agent_tag = f" ({agent.upper()})" if agent else ""
        if action_type == "decision":
            _slack_reply(
                f":white_check_mark: Noted your choice{agent_tag}: *option {value}*. "
                f"Will execute on next action cycle.",
                agent=agent,
            )
        elif action_type == "yes_no":
            _slack_reply(
                f":white_check_mark: Got your answer{agent_tag}: *{value}*. "
                f"Will proceed accordingly.",
                agent=agent,
            )
        elif action_type == "skip":
            _slack_reply(
                f":fast_forward: Will skip *{value}*{agent_tag}. Noted.",
                agent=agent,
            )
        elif action_type == "instruction":
            _slack_reply(
                f":memo: Received your instruction{agent_tag}. "
                f"Will act on it in my next cycle:\n> _{text[:200]}_",
                agent=agent,
            )

    _save_json(path, inbox)
    return count


def process_once():
    """Scan ALL agent inbox files and process unread messages from each.

    Returns total count processed across all inboxes.
    """
    total = 0
    for path in _all_inbox_files():
        total += _process_inbox_file(path)
    return total


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
