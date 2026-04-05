#!/usr/bin/env python3
"""Block until a new Slack message arrives or timeout expires.

Used by the AI agent to stay alive and responsive to Slack input.
Polls /tmp/jarvis-pending-actions.json every few seconds. Exits as
soon as an unprocessed action appears (exit code 1) or when the
timeout expires with nothing to do (exit code 0).

Usage:
    python skills/slack/wait_for_input.py           # 2 min timeout
    python skills/slack/wait_for_input.py --timeout 300  # 5 min
    python skills/slack/wait_for_input.py --poll 3       # check every 3s
"""

import argparse
import json
import sys
import time

PENDING = "/tmp/jarvis-pending-actions.json"
INBOX = "/tmp/jarvis-slack-inbox.json"


def check_pending():
    """Return list of unprocessed pending actions."""
    try:
        with open(PENDING) as f:
            items = json.load(f)
        return [i for i in items if not i.get("processed_by_ai")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def check_inbox():
    """Return list of unread inbox messages."""
    try:
        with open(INBOX) as f:
            items = json.load(f)
        return [i for i in items if not i.get("read")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=120,
                        help="Max seconds to wait (default: 120)")
    parser.add_argument("--poll", type=int, default=5,
                        help="Seconds between checks (default: 5)")
    args = parser.parse_args()

    start = time.time()
    while time.time() - start < args.timeout:
        items = check_pending()
        if items:
            for item in items:
                print(json.dumps(item))
            sys.exit(1)

        unread = check_inbox()
        if unread:
            for msg in unread:
                print(json.dumps(msg))
            sys.exit(2)

        time.sleep(args.poll)

    print("TIMEOUT")
    sys.exit(0)


if __name__ == "__main__":
    main()
