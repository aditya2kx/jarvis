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
    - chat:write also covers chat.delete (for removing credential messages after storage)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import load_config

SLACK_API = "https://slack.com/api"
_token_cache = None


def _get_bot_token():
    """Retrieve the Slack bot token from macOS Keychain."""
    global _token_cache
    if _token_cache:
        return _token_cache

    cfg = load_config()
    cmd = cfg.get("slack", {}).get(
        "bot_token_cmd",
        "security find-generic-password -a SLACK_BOT_TOKEN -s jarvis -w",
    )
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            _token_cache = result.stdout.strip()
            return _token_cache
        raise RuntimeError(f"Keychain lookup failed: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Keychain lookup timed out")


def _api_call(method, params=None, json_body=None):
    """Make a Slack API call."""
    token = _get_bot_token()
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


def send_message(channel, text, thread_ts=None):
    """Send a message to a Slack channel or DM.

    Args:
        channel: Channel ID, channel name (e.g. '#general'), or user ID for DM
        text: Message text (supports Slack markdown)
        thread_ts: Optional thread timestamp for threaded replies

    Returns:
        dict with 'ok', 'ts' (message timestamp), 'channel'
    """
    body = {"channel": channel, "text": text}
    if thread_ts:
        body["reply_broadcast"] = False
        body["thread_ts"] = thread_ts
    result = _api_call("chat.postMessage", json_body=body)
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


def open_dm(user_id):
    """Open a DM channel with a user.

    Args:
        user_id: Slack user ID

    Returns:
        Channel ID for the DM conversation
    """
    result = _api_call("conversations.open", json_body={"users": user_id})
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


def request_otp(user_id, portal_name, timeout_seconds=300, poll_interval=10, phone_hint=None):
    """Send an OTP request via Slack DM and wait for the user's reply.

    Tries Socket Mode (push) first for instant delivery, falls back to polling.

    Args:
        user_id: Slack user ID to DM
        portal_name: Name of the portal requesting OTP (e.g. 'Schwab')
        timeout_seconds: How long to wait for a reply (default 5 min)
        poll_interval: Seconds between polling for replies
        phone_hint: Optional masked phone number (e.g. '+1-XXX-XXX-XXXX')

    Returns:
        The OTP code as a string, or None if timed out
    """
    dm_channel = open_dm(user_id)

    phone_line = f"\nA verification code was sent to your phone ({phone_hint})." if phone_hint else ""
    msg = send_message(
        dm_channel,
        f":key: *OTP Required — {portal_name}*\n\n"
        f"Chitra is logging into {portal_name} and needs your verification code.{phone_line}\n"
        f"Please reply here with the code within {timeout_seconds // 60} minutes.",
    )
    sent_ts = msg["ts"]

    try:
        from skills.slack.listener import is_socket_mode_available, read_otp
        if is_socket_mode_available():
            print(f"[otp] Using Socket Mode (push) for {portal_name}")
            otp = read_otp(portal_name, timeout=timeout_seconds)
            if otp:
                return otp
            send_message(dm_channel, f":x: Timed out waiting for {portal_name} OTP after {timeout_seconds // 60} minutes.")
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
                send_message(dm_channel, f":white_check_mark: Got it — using code `{otp}`")
                return otp

    send_message(dm_channel, f":x: Timed out waiting for {portal_name} OTP after {timeout_seconds // 60} minutes.")
    return None


def test_connection():
    """Verify the Slack bot token works by calling auth.test.

    Returns:
        dict with bot info (team, user, url) if successful
    """
    result = _api_call("auth.test")
    if not result.get("ok"):
        raise RuntimeError(f"auth.test failed: {result.get('error', 'unknown')}")
    return {
        "ok": True,
        "team": result.get("team"),
        "user": result.get("user"),
        "url": result.get("url"),
    }


if __name__ == "__main__":
    info = test_connection()
    print(f"Connected to Slack workspace: {info['team']}")
    print(f"Bot user: {info['user']}")
    print(f"URL: {info['url']}")
