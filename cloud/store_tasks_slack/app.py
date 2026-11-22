"""Slack ↔ Store Tasks bridge. The Tasks API knows nothing about Slack; this service does.

    Tasks → Slack   POST /events             Pub/Sub push of task events → a post in the store channel
    Slack → Tasks   POST /slack/commands     /task [shift:] text         → a one-off task for today
                    POST /slack/interactions message shortcut "Make this a task"

Config (env): SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, STORE_TASKS_API_URL,
STORE_TASKS_INTERNAL_TOKEN, STORE_TASKS_PUSH_TOKEN, STORE_TASKS_SLACK_CHANNELS
(JSON ``{"<store>": "<channel id>"}``), STORE_TASKS_APP_URL (link shown in posts).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
import urllib.request

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("store_tasks_slack")

MAX_SKEW_SECONDS = 300
SHORTCUT = "make_task"


def channels() -> dict[str, str]:
    return json.loads(os.environ.get("STORE_TASKS_SLACK_CHANNELS") or "{}")


def store_for_channel(channel_id: str) -> str | None:
    return next((s for s, c in channels().items() if c == channel_id), None)


def signed_by_slack(body: bytes, timestamp: str, signature: str, now: float | None = None) -> bool:
    secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    if not secret or not timestamp.isdigit() or abs((now or time.time()) - int(timestamp)) > MAX_SKEW_SECONDS:
        return False
    want = "v0=" + hmac.new(secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature)


def _post_json(url: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def slack_post(channel: str, text: str, thread_ts: str | None = None) -> None:
    out = _post_json("https://slack.com/api/chat.postMessage", os.environ["SLACK_BOT_TOKEN"],
                     {"channel": channel, "text": text, "thread_ts": thread_ts, "unfurl_links": False})
    if not out.get("ok"):
        raise RuntimeError(f"slack chat.postMessage: {out.get('error')}")


def create_task(store: str, title: str, shift: str | None, source: str, key: str) -> dict:
    """``key`` makes the call idempotent: Slack retries create the same task, not a second one."""
    base = os.environ["STORE_TASKS_API_URL"].rstrip("/")
    return _post_json(f"{base}/internal/stores/{store}/tasks", os.environ["STORE_TASKS_INTERNAL_TOKEN"],
                      {"title": title, "shift": shift, "source": source, "key": key})


def format_event(event: dict) -> str | None:
    """Slack text for an event, or None for events that should not post (every tick would be noise)."""
    link = os.environ.get("STORE_TASKS_APP_URL", "")
    more = f"\n<{link}|Open tasks>" if link else ""
    kind = event.get("type")
    if kind == "shift.incomplete":
        titles = "\n".join(f"• {t}" for t in event.get("titles") or [])
        extra = event["open"] - len(event.get("titles") or [])
        tail = f"\n…and {extra} more" if extra > 0 else ""
        return (f":warning: *{event['label']}* is past due with {event['open']} of {event['total']} "
                f"tasks still open ({event['date']}).\n{titles}{tail}{more}")
    if kind == "count.submitted":
        notes = event.get("notes") or {}
        lines = [f":clipboard: *Closing count sent* for {event['date']} — {event['items']} items."]
        for key, label in (("out", "Out of"), ("low", "Running low"), ("shift", "Shift notes")):
            if (notes.get(key) or "").strip():
                lines.append(f"*{label}:* {notes[key].strip()}")
        return "\n".join(lines)
    if kind == "task.created" and event.get("source") != "slack":
        return f":heavy_plus_sign: New task for {event['date']} ({event['shift'].replace('_', ' ')}): *{event['title']}*{more}"
    return None


def split_shift(text: str, shift_ids: tuple[str, ...] = ("opening", "shift_change", "closing")) -> tuple[str | None, str]:
    """'closing: wipe the door' → ('closing', 'wipe the door'); no prefix → (None, text)."""
    head, sep, rest = text.partition(":")
    key = head.strip().lower().replace(" ", "_")
    if sep and key in shift_ids and rest.strip():
        return key, rest.strip()
    return None, text.strip()


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(ok=True, service="store-tasks-slack")

    @app.post("/events")
    def events():
        want = os.environ.get("STORE_TASKS_PUSH_TOKEN", "")
        if len(want) < 24 or not hmac.compare_digest(request.args.get("token", ""), want):
            return jsonify(error="unauthorized"), 401
        message = (request.get_json(silent=True) or {}).get("message") or {}
        try:
            event = json.loads(base64.b64decode(message.get("data") or "e30="))
        except ValueError:
            return jsonify(ok=True, skipped="bad payload")  # ack: a retry cannot fix it
        channel = channels().get(event.get("store") or "")
        text = format_event(event)
        if not channel or not text:
            return jsonify(ok=True, skipped="no channel or nothing to post")
        try:
            slack_post(channel, text)
        except Exception as e:
            log.error("store-tasks-slack fail reason=post type=%s store=%s err=%r", event.get("type"), event.get("store"), e)
            return jsonify(error="post failed"), 502  # Pub/Sub retries
        return jsonify(ok=True)

    def verified() -> bool:
        return signed_by_slack(request.get_data(), request.headers.get("X-Slack-Request-Timestamp", ""),
                               request.headers.get("X-Slack-Signature", ""))

    @app.post("/slack/commands")
    def commands():
        if not verified():
            return jsonify(error="bad signature"), 401
        store = store_for_channel(request.form.get("channel_id", ""))
        shift, title = split_shift(request.form.get("text", ""))
        if not store:
            return jsonify(response_type="ephemeral", text="Use this in your store's channel.")
        if not title:
            return jsonify(response_type="ephemeral", text="Say what needs doing, e.g. `/task closing: wipe the front door`")
        try:
            task = create_task(store, title, shift, "slack", request.form.get("trigger_id", ""))
        except Exception as e:
            log.error("store-tasks-slack fail reason=create_task store=%s err=%r", store, e)
            return jsonify(response_type="ephemeral", text="Could not add the task. Try again in a minute.")
        who = request.form.get("user_id", "")
        return jsonify(response_type="in_channel",
                       text=f":heavy_plus_sign: <@{who}> added to today's {task['shift'].replace('_', ' ')} list: *{task['title']}*")

    @app.post("/slack/interactions")
    def interactions():
        if not verified():
            return jsonify(error="bad signature"), 401
        payload = json.loads(request.form.get("payload") or "{}")
        if payload.get("type") != "message_action" or payload.get("callback_id") != SHORTCUT:
            return "", 200
        channel = (payload.get("channel") or {}).get("id", "")
        message = payload.get("message") or {}
        store = store_for_channel(channel)
        shift, title = split_shift(" ".join((message.get("text") or "").split())[:200])
        if not store or not title:
            return "", 200
        try:
            task = create_task(store, title, shift, "slack", f"{channel}-{message.get('ts')}")
            slack_post(channel, f":heavy_plus_sign: Added to today's {task['shift'].replace('_', ' ')} list.",
                       thread_ts=message.get("ts"))
        except Exception as e:
            log.error("store-tasks-slack fail reason=shortcut store=%s err=%r", store, e)
        return "", 200

    return app
