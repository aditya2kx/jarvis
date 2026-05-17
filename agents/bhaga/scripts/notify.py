#!/usr/bin/env python3
"""BHAGA Slack notification helpers.

Sends DMs as BHAGA. While the BHAGA Slack app does not yet exist, this falls
back to the default (CHITRA) bot and prefixes the message with the configured
display_prefix (e.g. "[BHAGA] ") so the user can tell agents apart in the same
DM thread. This is a TRANSITIONAL bridge — see config.yaml slack.agents.bhaga.

Once a real BHAGA Slack app is created and registered:
  1. Update config.yaml slack.agents.bhaga.bot_token_cmd to the new Keychain entry
  2. Update slack.agents.bhaga.dm_channel to the new BHAGA-user DM channel
  3. Set slack.agents.bhaga.identity_mode = "real"

After step 3 the prefix stops being applied because the bot user itself is BHAGA.

Usage:
    python agents/bhaga/scripts/notify.py "Pulled tips for 2026-04-18: $147.30"
    # or
    from agents.bhaga.scripts.notify import dm
    dm("Pulled tips for 2026-04-18: $147.30")
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from core.config_loader import load_config
from skills.slack.adapter import send_progress, set_agent


AGENT = "bhaga"


def _prefix():
    cfg = load_config()
    agent_cfg = cfg.get("slack", {}).get("agents", {}).get(AGENT, {})
    if agent_cfg.get("identity_mode", "transitional") == "real":
        return ""
    return agent_cfg.get("display_prefix", "[BHAGA] ")


def dm(text):
    """Send a DM to the user as BHAGA."""
    set_agent(AGENT)
    return send_progress(_prefix() + text)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: notify.py <message text>", file=sys.stderr)
        sys.exit(2)
    msg = " ".join(sys.argv[1:])
    result = dm(msg)
    print(f"sent ts={result.get('ts')} channel={result.get('channel')}")
