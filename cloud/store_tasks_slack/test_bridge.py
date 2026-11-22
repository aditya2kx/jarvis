"""Slack bridge: only Slack-signed requests act; events become posts; messages become tasks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

from cloud.store_tasks_slack import app as bridge

PUSH = "push-token-for-tests-0123456789"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shh")
    monkeypatch.setenv("STORE_TASKS_PUSH_TOKEN", PUSH)
    monkeypatch.setenv("STORE_TASKS_SLACK_CHANNELS", json.dumps({"palmetto": "C1"}))
    calls = {"posts": [], "tasks": []}
    monkeypatch.setattr(bridge, "slack_post", lambda ch, text, thread_ts=None: calls["posts"].append((ch, text, thread_ts)))
    monkeypatch.setattr(bridge, "create_task", lambda store, title, shift, source, key: (
        calls["tasks"].append((store, title, shift, key)) or {"title": title, "shift": shift or "opening"}))
    c = bridge.create_app().test_client()
    c.calls = calls
    return c


def signed(client, path, form, secret="shh"):
    body = urllib.parse.urlencode(form)
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return client.post(path, data=body, content_type="application/x-www-form-urlencoded",
                       headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})


def push(client, event, token=PUSH):
    data = base64.b64encode(json.dumps(event).encode()).decode()
    return client.post(f"/events?token={token}", json={"message": {"data": data}})


def test_unfinished_shift_and_count_post_but_ticks_do_not(client):
    assert push(client, {"type": "task.completed", "store": "palmetto"}).status_code == 200
    assert client.calls["posts"] == []
    push(client, {"type": "shift.incomplete", "store": "palmetto", "label": "Opening", "open": 12, "total": 27,
                  "date": "2026-11-19", "titles": ["Unlock", "Fill sink"]})
    push(client, {"type": "count.submitted", "store": "palmetto", "date": "2026-11-19", "items": 39,
                  "notes": {"out": "Water bottles", "low": "", "shift": ""}})
    (ch, alert, _), (_, count, _) = client.calls["posts"]
    assert ch == "C1" and "12 of 27" in alert and "• Unlock" in alert and "and 10 more" in alert
    assert "39 items" in count and "Out of:* Water bottles" in count and "Running low" not in count
    assert push(client, {"type": "shift.incomplete", "store": "palmetto"}, token="wrong").status_code == 401


def test_slash_command_creates_a_task_only_when_signed(client):
    form = {"channel_id": "C1", "user_id": "U1", "text": "Closing: wipe the door", "trigger_id": "t1"}
    assert signed(client, "/slack/commands", form, secret="forged").status_code == 401
    out = signed(client, "/slack/commands", form).get_json()
    assert client.calls["tasks"] == [("palmetto", "wipe the door", "closing", "t1")]
    assert out["response_type"] == "in_channel" and "wipe the door" in out["text"]
    other = signed(client, "/slack/commands", {**form, "channel_id": "C9"}).get_json()
    assert other["response_type"] == "ephemeral" and len(client.calls["tasks"]) == 1


def test_message_shortcut_makes_a_task_and_replies_in_thread(client):
    payload = {"type": "message_action", "callback_id": "make_task", "channel": {"id": "C1"},
               "message": {"text": "freezer door\nis sticking", "ts": "171.5"}}
    assert signed(client, "/slack/interactions", {"payload": json.dumps(payload)}).status_code == 200
    assert client.calls["tasks"] == [("palmetto", "freezer door is sticking", None, "C1-171.5")]
    assert client.calls["posts"][0][2] == "171.5"
