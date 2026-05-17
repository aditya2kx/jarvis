#!/usr/bin/env python3
"""Slack app provisioning — post-Playwright finalizer.

After the AI drives `user-playwright` through Slack's app-creation flow and
captures `bot_token` (xoxb-...) and `app_token` (xapp-...), call
`register_agent_identity()` to perform the full finalization in one transaction:

    1. Store both tokens in macOS Keychain (via skills/credentials)
    2. Resolve the agent's DM channel by opening a DM with the user using the
       new bot token
    3. Update config.yaml's slack.agents.<name> entry with real bot_token_cmd,
       real dm_channel, and identity_mode = "real" (which automatically stops
       the [AGENT] text prefix from being applied)
    4. Send a confirmation DM as the new bot user

Idempotent: re-running with the same agent + tokens overwrites Keychain entries
and config without duplicating; safe to retry after partial failure.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request
from typing import Optional

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import load_config, project_dir
from skills.credentials import registry as cred_registry


SLACK_API = "https://slack.com/api"


# ── Keychain ───────────────────────────────────────────────────────


def _service(agent: str) -> str:
    return f"jarvis-{agent.lower()}"


def _bot_account(agent: str) -> str:
    return f"SLACK_BOT_TOKEN_{agent.upper()}"


def _app_account(agent: str) -> str:
    return f"SLACK_APP_TOKEN_{agent.upper()}"


def _bot_token_cmd(agent: str) -> str:
    return (
        f"security find-generic-password -a {_bot_account(agent)} "
        f"-s {_service(agent)} -w"
    )


def _app_token_cmd(agent: str) -> str:
    return (
        f"security find-generic-password -a {_app_account(agent)} "
        f"-s {_service(agent)} -w"
    )


def store_tokens(agent: str, bot_token: str, app_token: str) -> dict:
    """Store both Slack tokens in Keychain via skills/credentials."""
    if not bot_token or not bot_token.startswith("xoxb-"):
        raise ValueError(f"bot_token must start with 'xoxb-', got: {bot_token!r}")
    if not app_token or not app_token.startswith("xapp-"):
        raise ValueError(f"app_token must start with 'xapp-', got: {app_token!r}")

    bot_entry = cred_registry.add_keychain(
        name=f"slack_{agent.lower()}_bot",
        service=_service(agent),
        account=_bot_account(agent),
        password=bot_token,
        portal=f"slack-{agent.lower()}",
        notes=f"Slack bot token for the {agent} agent's separate Slack app",
    )
    app_entry = cred_registry.add_keychain(
        name=f"slack_{agent.lower()}_app",
        service=_service(agent),
        account=_app_account(agent),
        password=app_token,
        portal=f"slack-{agent.lower()}",
        notes=(
            f"Slack app-level token (xapp-...) for the {agent} agent — "
            f"used by Socket Mode listener for push delivery"
        ),
    )
    return {"bot": bot_entry, "app": app_entry}


# ── DM channel resolution ──────────────────────────────────────────


def open_dm(bot_token: str, user_id: str) -> str:
    """Open a DM between the agent's bot user and `user_id`. Returns channel ID."""
    body = json.dumps({"users": user_id}).encode()
    req = urllib.request.Request(
        f"{SLACK_API}/conversations.open",
        data=body,
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if not data.get("ok"):
        raise RuntimeError(f"conversations.open failed: {data.get('error')}")
    return data["channel"]["id"]


def send_first_dm(bot_token: str, channel_id: str, agent: str) -> dict:
    """Send the agent's first DM as its real bot user (no [AGENT] prefix)."""
    text = (
        f":wave: *{agent.upper()}* is online — this message is from my own Slack bot user "
        f"(not via CHITRA). The transitional `[{agent.upper()}]` text prefix is now disabled. "
        f"You'll see me as a separate identity in your Slack sidebar from here on."
    )
    body = json.dumps({"channel": channel_id, "text": text}).encode()
    req = urllib.request.Request(
        f"{SLACK_API}/chat.postMessage",
        data=body,
        headers={
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ── config.yaml ────────────────────────────────────────────────────


def _config_path() -> pathlib.Path:
    return pathlib.Path(project_dir()) / "config.yaml"


def update_config(agent: str, dm_channel: str) -> None:
    """Flip slack.agents.<agent> to real-identity values."""
    path = _config_path()
    cfg = yaml.safe_load(path.read_text()) or {}
    slack_cfg = cfg.setdefault("slack", {})
    agents_cfg = slack_cfg.setdefault("agents", {})
    entry = agents_cfg.get(agent.lower(), {})

    entry["bot_token_cmd"] = _bot_token_cmd(agent)
    entry["dm_channel"] = dm_channel
    entry["identity_mode"] = "real"
    # display_prefix is no longer applied when identity_mode == "real"; we leave
    # the key in place (commented in YAML output isn't portable; explicit empty
    # string is the clearest "intentionally inert" marker).
    entry["display_prefix"] = ""

    agents_cfg[agent.lower()] = entry
    cfg["slack"]["agents"] = agents_cfg

    # Preserve YAML formatting reasonably; safe_dump default is fine for our
    # config since it has no anchors / merges.
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))


# ── Top-level orchestrator ─────────────────────────────────────────


def register_agent_identity(
    agent_name: str,
    bot_token: str,
    app_token: str,
    user_id: Optional[str] = None,
) -> dict:
    """One call → fully wired real Slack identity for the agent.

    Returns a summary dict with all the artifacts written.
    """
    cfg = load_config()
    if user_id is None:
        user_id = cfg.get("slack", {}).get("primary_user_id")
        if not user_id:
            raise RuntimeError(
                "primary_user_id not in config.yaml; pass user_id explicitly"
            )

    keychain = store_tokens(agent_name, bot_token, app_token)
    dm_channel = open_dm(bot_token, user_id)
    update_config(agent_name, dm_channel)
    first_dm = send_first_dm(bot_token, dm_channel, agent_name)

    return {
        "agent": agent_name.lower(),
        "keychain": keychain,
        "dm_channel": dm_channel,
        "config_path": str(_config_path()),
        "first_dm_ts": first_dm.get("ts"),
        "first_dm_ok": first_dm.get("ok", False),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Slack agent identity register")
    parser.add_argument("--agent", required=True, help="Agent name (e.g. bhaga)")
    parser.add_argument("--bot-token", required=True, help="xoxb-... bot token")
    parser.add_argument("--app-token", required=True, help="xapp-... app-level token")
    parser.add_argument("--user-id", default=None, help="Override primary_user_id")
    args = parser.parse_args()

    summary = register_agent_identity(
        agent_name=args.agent,
        bot_token=args.bot_token,
        app_token=args.app_token,
        user_id=args.user_id,
    )
    print(json.dumps(summary, indent=2, default=str))
