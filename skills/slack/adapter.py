#!/usr/bin/env python3
"""Slack communication skill for Jarvis agents.

Provides functions to send messages, read replies, and manage OTP flows
via the Slack API. Bot token is retrieved from macOS Keychain.

Usage by agents:
    from skills.slack.adapter import send_message, read_replies, request_otp

Prerequisites:
    - Slack app created and installed to workspace
    - Bot token stored in Keychain: security add-generic-password -a SLACK_BOT_TOKEN -s jarvis -w "xoxb-..."
    - Bot scopes: chat:write, channels:read, im:write, im:read, im:history, users:read
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import load_config

SLACK_API = "https://slack.com/api"
_token_cache = {}
_current_agent = None


def set_agent(agent_name):
    """Set the active agent for Slack messages.

    Each agent has its own Slack app and bot token, stored in Keychain
    under a separate service name. This gives each agent a distinct
    profile (name, avatar) in Slack.
    """
    global _current_agent
    _current_agent = agent_name.lower() if agent_name else None


def _get_bot_token(agent=None):
    """Retrieve the Slack bot token from macOS Keychain.

    Each agent has its own Slack app and bot token. The keychain
    account name follows the pattern: SLACK_BOT_TOKEN_<AGENT>.
    Falls back to the default SLACK_BOT_TOKEN for backward compat.
    """
    agent_key = (agent or _current_agent or "default").lower()
    if agent_key in _token_cache:
        return _token_cache[agent_key]

    cfg = load_config()
    agents_cfg = cfg.get("slack", {}).get("agents", {})

    if agent_key in agents_cfg:
        cmd = agents_cfg[agent_key].get("bot_token_cmd", "")
    else:
        cmd = cfg.get("slack", {}).get(
            "bot_token_cmd",
            "security find-generic-password -a SLACK_BOT_TOKEN -s jarvis -w",
        )

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            _token_cache[agent_key] = result.stdout.strip()
            return _token_cache[agent_key]
        raise RuntimeError(f"Keychain lookup failed for agent '{agent_key}': {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Keychain lookup timed out for agent '{agent_key}'")


def _api_call(method, params=None, json_body=None, agent=None):
    """Make a Slack API call."""
    token = _get_bot_token(agent=agent)
    url = f"{SLACK_API}/{method}"

    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
    elif params:
        data = urllib.parse.urlencode(params).encode()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
    else:
        data = None
        headers = {"Authorization": f"Bearer {token}"}

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise RuntimeError(f"Slack API {method} failed ({e.code}): {body}")


def send_message(channel, text, thread_ts=None, agent=None):
    """Send a message to a Slack channel or DM.

    Args:
        channel: Channel ID, channel name (e.g. '#general'), or user ID for DM
        text: Message text (supports Slack markdown)
        thread_ts: Optional thread timestamp for threaded replies
        agent: Agent name to send as (e.g. 'chanakya'). Each agent has
               its own Slack app/bot. If None, uses set_agent() value
               or falls back to the default (chitra) bot.

    Returns:
        dict with 'ok', 'ts' (message timestamp), 'channel'
    """
    body = {"channel": channel, "text": text}
    if thread_ts:
        body["reply_broadcast"] = False
        body["thread_ts"] = thread_ts
    effective_agent = agent or _current_agent
    result = _api_call("chat.postMessage", json_body=body, agent=effective_agent)
    if not result.get("ok"):
        raise RuntimeError(f"send_message failed: {result.get('error', 'unknown')}")
    return result


def read_replies(channel, thread_ts=None, limit=10, oldest=None):
    """Read recent messages from a channel or thread.

    Args:
        channel: Channel ID
        thread_ts: If provided, reads thread replies; otherwise reads channel history
        limit: Max messages to return
        oldest: Only return messages after this timestamp

    Returns:
        List of message dicts with 'text', 'user', 'ts'
    """
    if thread_ts:
        params = {"channel": channel, "ts": thread_ts, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        result = _api_call("conversations.replies", params=params)
    else:
        params = {"channel": channel, "limit": limit}
        if oldest:
            params["oldest"] = oldest
        result = _api_call("conversations.history", params=params)

    if not result.get("ok"):
        raise RuntimeError(f"read_replies failed: {result.get('error', 'unknown')}")
    return result.get("messages", [])


def open_dm(user_id, agent=None):
    """Open a DM channel with a user.

    Args:
        user_id: Slack user ID
        agent: Agent name whose bot token to use

    Returns:
        Channel ID for the DM conversation
    """
    effective_agent = agent or _current_agent
    result = _api_call("conversations.open", json_body={"users": user_id}, agent=effective_agent)
    if not result.get("ok"):
        raise RuntimeError(f"open_dm failed: {result.get('error', 'unknown')}")
    return result["channel"]["id"]


def find_user(display_name=None, email=None):
    """Find a Slack user by display name or email.

    Args:
        display_name: User's display name to search for
        email: User's email address

    Returns:
        User ID if found, None otherwise
    """
    result = _api_call("users.list")
    if not result.get("ok"):
        raise RuntimeError(f"find_user failed: {result.get('error', 'unknown')}")

    for member in result.get("members", []):
        if member.get("deleted") or member.get("is_bot"):
            continue
        profile = member.get("profile", {})
        if email and profile.get("email") == email:
            return member["id"]
        if display_name:
            name = profile.get("display_name", "") or member.get("real_name", "")
            if display_name.lower() in name.lower():
                return member["id"]
    return None


def request_otp(user_id, portal_name, timeout_seconds=300, poll_interval=10, phone_hint=None, agent=None):
    """Send an OTP request via Slack DM and wait for the user's reply.

    Tries Socket Mode (push) first for instant delivery, falls back to polling.

    Args:
        user_id: Slack user ID to DM
        portal_name: Name of the portal requesting OTP (e.g. 'Schwab')
        timeout_seconds: How long to wait for a reply (default 5 min)
        poll_interval: Seconds between polling for replies
        phone_hint: Optional masked phone number (e.g. '+1-XXX-XXX-XXXX')
        agent: Agent name to send as

    Returns:
        The OTP code as a string, or None if timed out
    """
    effective_agent = agent or _current_agent
    agent_display = (effective_agent or "Jarvis").capitalize()
    dm_channel = open_dm(user_id, agent=effective_agent)

    phone_line = f"\nA verification code was sent to your phone ({phone_hint})." if phone_hint else ""
    msg = send_message(
        dm_channel,
        f":key: *OTP Required — {portal_name}*\n\n"
        f"{agent_display} is logging into {portal_name} and needs your verification code.{phone_line}\n"
        f"Please reply here with the code within {timeout_seconds // 60} minutes.",
        agent=effective_agent,
    )
    sent_ts = msg["ts"]

    try:
        from skills.slack.listener import is_socket_mode_available, read_otp
        if is_socket_mode_available():
            print(f"[otp] Using Socket Mode (push) for {portal_name}")
            otp = read_otp(portal_name, timeout=timeout_seconds)
            if otp:
                return otp
            send_message(dm_channel, f":x: Timed out waiting for {portal_name} OTP after {timeout_seconds // 60} minutes.", agent=effective_agent)
            return None
    except ImportError:
        pass

    print(f"[otp] Socket Mode not available, falling back to polling for {portal_name}")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(poll_interval)
        replies = read_replies(dm_channel, oldest=sent_ts, limit=5)
        for reply in replies:
            if reply["ts"] != sent_ts and reply.get("user") == user_id:
                otp = reply["text"].strip()
                send_message(dm_channel, f":white_check_mark: Got it — using code `{otp}`", agent=effective_agent)
                return otp

    send_message(dm_channel, f":x: Timed out waiting for {portal_name} OTP after {timeout_seconds // 60} minutes.", agent=effective_agent)
    return None


def check_for_user_messages(since_ts=None, dm_channel=None, user_id=None, agent=None):
    """Check for new messages from the user in the DM channel.

    Args:
        since_ts: Only return messages newer than this timestamp.
                  If None, reads the last 5 messages.
        dm_channel: Override DM channel (default: from config)
        user_id: Override user ID (default: from config)
        agent: Agent name whose DM channel to check

    Returns:
        List of message dicts from the user (newest first), each with
        'text', 'ts', 'user'. Empty list if no new messages.
    """
    cfg = load_config()
    effective_agent = agent or _current_agent
    channel = dm_channel or _get_agent_dm_channel(effective_agent)
    uid = user_id or cfg.get("slack", {}).get("primary_user_id")

    if not channel:
        return []

    messages = read_replies(channel, limit=10, oldest=since_ts)
    user_msgs = [
        m for m in messages
        if m.get("user") == uid and not m.get("bot_id")
    ]
    return user_msgs


def _get_agent_dm_channel(agent=None):
    """Resolve the DM channel for the given agent from config."""
    cfg = load_config()
    agent_key = (agent or _current_agent or "").lower()
    if agent_key:
        agents_cfg = cfg.get("slack", {}).get("agents", {})
        ch = agents_cfg.get(agent_key, {}).get("dm_channel", "")
        if ch:
            return ch
    return cfg.get("slack", {}).get("dm_channel")


def send_progress(text, dm_channel=None, agent=None):
    """Send a progress update to the user's DM. Convenience wrapper.

    Routes through the correct agent's bot token and DM channel.
    """
    effective_agent = agent or _current_agent
    channel = dm_channel or _get_agent_dm_channel(effective_agent)
    if channel:
        return send_message(channel, text, agent=effective_agent)


def ask_user(question, dm_channel=None, poll_interval=20, agent=None):
    """Send a question and wait indefinitely for the user's reply.

    HARD LESSON: Never timebox user input. Wait forever — the user
    will reply when they can.

    Args:
        question: The question text to send
        dm_channel: Override DM channel
        poll_interval: Seconds between polls (default 20)
        agent: Agent name to send as

    Returns:
        The user's reply text (never returns None — waits indefinitely)
    """
    cfg = load_config()
    effective_agent = agent or _current_agent
    channel = dm_channel or _get_agent_dm_channel(effective_agent)
    uid = cfg.get("slack", {}).get("primary_user_id")

    if not channel:
        raise RuntimeError("No DM channel configured")

    msg = send_message(channel, question, agent=effective_agent)
    sent_ts = msg["ts"]

    while True:
        time.sleep(poll_interval)
        replies = read_replies(channel, oldest=sent_ts, limit=5)
        for reply in replies:
            if reply["ts"] != sent_ts and reply.get("user") == uid:
                return reply["text"].strip()


def read_inbox(mark_read=True):
    """Read unread messages from the Socket Mode listener's inbox.

    The listener runs as a background daemon and queues all non-command
    user messages to /tmp/jarvis-slack-inbox.json. This function reads them.

    Returns:
        List of message dicts with 'text', 'user', 'ts'. Empty if no unread.
    """
    try:
        from skills.slack.listener import read_inbox as _read
        return _read(mark_read)
    except ImportError:
        return []


def test_connection(agent=None):
    """Verify the Slack bot token works by calling auth.test.

    Args:
        agent: Agent name whose token to test

    Returns:
        dict with bot info (team, user, url) if successful
    """
    effective_agent = agent or _current_agent
    result = _api_call("auth.test", agent=effective_agent)
    if not result.get("ok"):
        raise RuntimeError(f"auth.test failed: {result.get('error', 'unknown')}")
    return {
        "ok": True,
        "team": result.get("team"),
        "user": result.get("user"),
        "bot_id": result.get("bot_id"),
        "url": result.get("url"),
    }


if __name__ == "__main__":
    import sys
    agent_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if agent_arg:
        set_agent(agent_arg)
    info = test_connection()
    print(f"Connected to Slack workspace: {info['team']}")
    print(f"Bot user: {info['user']}")
    print(f"URL: {info['url']}")
