#!/usr/bin/env python3
"""Reach the operator on Slack from any agent surface when a step needs a human.

    python3 scripts/operator_ping.py "Google 2-Step for Cloud Shell: tap Yes on your phone"
    python3 scripts/operator_ping.py "Reply with the 6-digit Google code (expires in ~5 min)" \
        --wait 600 --type --enter          # click the browser field first; the code goes to the keyboard
    python3 scripts/operator_ping.py --match-number 41 "Cloud Shell sign-in"
        # Google "Match the number": send the number unasked as soon as it shows, and again after every resend

The established operator-in-the-loop channel (jarvis-hard-lessons.mdc #7): a sign-in that needs a
code, a phone tap or a click DMs the operator through the BHAGA cloud bot and waits for the reply —
never stop and hand back in chat. Email codes: read them from the inbox instead (Gmail).

Token: `SLACK_BOT_TOKEN` (Cloud Run / laptop), else Secret Manager `slack-bot-token` through the
Cursor Cloud Agent identity (`scripts/cloud_agent_gcp.py`; grant in
`scripts/provision_cloud_agent_identity.sh`). A reply is typed (`--type`) or printed — never both,
and codes are never logged. Exit 0 = sent (and, with --wait, answered); 2 = no reply; 1 = no token.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import cloud_agent_gcp as G  # noqa: E402
from skills.slack import adapter  # noqa: E402
from skills.vault.vault import type_text  # noqa: E402

AGENT = "bhaga"
OPERATOR = os.environ.get("BHAGA_OPERATOR_USER_ID", "U0APJRE5DC4")
PREFIX = ":robot_face: *[Cloud Agent]* "


def ensure_token() -> None:
    if os.environ.get("SLACK_BOT_TOKEN"):
        return
    federated = G.federated_access_token(G.cursor_oidc_token())
    os.environ["SLACK_BOT_TOKEN"] = G.access_secret("slack-bot-token", G.sa_access_token(federated))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("message")
    ap.add_argument("--wait", type=int, default=0, help="seconds to wait for a reply (0 = just notify)")
    ap.add_argument("--type", action="store_true", help="type the reply into the focused window instead of printing it")
    ap.add_argument("--enter", action="store_true")
    ap.add_argument("--match-number", help="Google 'Match the number' value shown on the sign-in page")
    a = ap.parse_args(argv)
    try:
        ensure_token()
    except Exception as e:  # noqa: BLE001
        print(f"operator_ping: no Slack token — {str(e)[:200]}", file=sys.stderr)
        return 1
    text = PREFIX + a.message
    if a.match_number:
        text = (f"{PREFIX}*Google sign-in — tap {a.match_number}* on your phone ({a.message}). "
                "A resend shows a new number; I'll send it too.")
    if not a.wait:
        adapter.send_message(adapter.open_dm(OPERATOR, agent=AGENT), text, agent=AGENT)
        print("operator_ping: sent")
        return 0
    reply = adapter.request_reply(OPERATOR, text + "\n_Reply here._", timeout_seconds=a.wait, agent=AGENT)
    if reply is None:
        print(f"operator_ping: no reply in {a.wait}s", file=sys.stderr)
        return 2
    if a.type:
        type_text(reply, a.enter)
        print("operator_ping: typed the reply")
    else:
        print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
