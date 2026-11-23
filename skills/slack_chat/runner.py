"""Slack Web API client for the staff workspace (palmettosuperfoods.slack.com).

Same shape as ``skills/clickup_chat`` so a store's automations can post to Slack
instead of ClickUp: ``post_message``, ``open_dm``, ``list_members``.

Token: ``PALMETTO_SLACK_BOT_TOKEN`` (Cloud Run secret mount), else Secret Manager
``store-tasks-slack-bot-token`` — the one Palmetto bot shared with the Store Tasks
Slack bridge (``cloud/store_tasks_slack``).
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

API_BASE = "https://slack.com/api"
TOKEN_ENV_VAR = "PALMETTO_SLACK_BOT_TOKEN"
TOKEN_SECRET = "store-tasks-slack-bot-token"
GCP_PROJECT = "jarvis-bhaga-prod"


def get_token() -> str:
    tok = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if tok:
        return tok
    from google.cloud import secretmanager

    path = f"projects/{GCP_PROJECT}/secrets/{TOKEN_SECRET}/versions/latest"
    resp = secretmanager.SecretManagerServiceClient().access_secret_version(request={"name": path})
    return resp.payload.data.decode().strip()


def _call(method: str, *, token: str | None = None, payload: dict | None = None,
          query: dict | None = None) -> dict:
    url = f"{API_BASE}/{method}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"Authorization": f"Bearer {token or get_token()}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json; charset=utf-8"
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=30) as resp:
        out = json.loads(resp.read().decode())
    if not out.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {out.get('error')}")
    return out


def to_mrkdwn(text: str) -> str:
    """ClickUp-flavoured markdown → Slack mrkdwn (bold, bullets, @everyone)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^\*\s{2,}", "• ", text, flags=re.M)
    return re.sub(r"@everyone\b", "<!channel>", text)


def mention(user_id: str) -> str:
    return f"<@{user_id}>"


def post_message(channel_id: str, content: str, *, token: str | None = None) -> dict:
    """Post ``content`` (converted to mrkdwn); returns ``{"id": ts, "channel": id}``."""
    out = _call("chat.postMessage", token=token, payload={
        "channel": channel_id, "text": to_mrkdwn(content), "unfurl_links": False,
    })
    return {"id": out.get("ts"), "channel": out.get("channel")}


def open_dm(user_id: str, *, token: str | None = None) -> str:
    return _call("conversations.open", token=token, payload={"users": user_id})["channel"]["id"]


def list_members(*, token: str | None = None) -> list[dict]:
    """Active humans as ``{"id", "username", "email"}`` — ``username`` is the full name."""
    out: list[dict] = []
    cursor = ""
    while True:
        page = _call("users.list", token=token, query={"limit": 200, "cursor": cursor})
        for m in page.get("members", []):
            if m.get("deleted") or m.get("is_bot") or m.get("id") == "USLACKBOT":
                continue
            prof = m.get("profile") or {}
            name = prof.get("real_name") or m.get("real_name") or prof.get("display_name") or m.get("name") or ""
            out.append({"id": m["id"], "username": name, "email": prof.get("email") or ""})
        cursor = (page.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return out
