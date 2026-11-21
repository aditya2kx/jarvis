"""Tell the operator on Slack when the crew sends feedback: a DM from the BHAGA cloud bot.

Optional: without SLACK_BOT_TOKEN and STORE_TASKS_FEEDBACK_SLACK_USER nothing is sent, and a
failed DM never fails the feedback itself (it is already stored and listed in Manage → Feedback).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import urllib.request
from zoneinfo import ZoneInfo

from cloud.store_tasks import model

log = logging.getLogger("store_tasks")

SLACK = "https://slack.com/api/"
KIND_LABEL = {"problem": "Something's wrong", "idea": "Idea"}


def _slack(token: str, method: str, payload: dict) -> dict:
    req = urllib.request.Request(SLACK + method, data=json.dumps(payload).encode(), headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=8) as r:
        out = json.load(r)
    if not out.get("ok"):
        raise RuntimeError(f"{method}: {out.get('error')}")
    return out


def feedback_text(cfg: model.StoreConfig, f: dict, url: str) -> str:
    ctx = f.get("context") or {}
    at = datetime.datetime.fromisoformat(f["at"]).astimezone(ZoneInfo(cfg.tz))
    shift = next((s.label for s in cfg.shifts if s.id == ctx.get("shift")), None)
    where = [ctx.get("screen"), ctx.get("date"), shift and f"{shift} list", ctx.get("task") and f"task “{ctx['task']}”"]
    who = f["staff_name"] + (f" on {f['device']}" if f.get("device") else "")
    quoted = "\n".join(f"> {line}" for line in (f["text"] or "(photo only)").splitlines())
    lines = [f"*Store Tasks feedback* · {KIND_LABEL.get(f['kind'], f['kind'])} · {cfg.name}", quoted,
             f"{who} · {at:%a %b %-d, %-I:%M %p} · " + " · ".join(w for w in where if w),
             f"{len(f.get('events') or [])} recent steps captured"
             + (f" · {len(f['photos'])} photo" if f.get("photos") else "") + f" · id `{f['id']}`"]
    if url:
        lines.append(f"<{url}|Open Store Tasks> → Manage → Feedback")
    return "\n".join(lines)


def feedback(cfg: model.StoreConfig, f: dict) -> bool:
    token, user = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("STORE_TASKS_FEEDBACK_SLACK_USER")
    if not (token and user):
        return False
    try:
        channel = _slack(token, "conversations.open", {"users": user})["channel"]["id"]
        _slack(token, "chat.postMessage", {"channel": channel, "unfurl_links": False,
                                           "text": feedback_text(cfg, f, os.environ.get("STORE_TASKS_HOME_URL", ""))})
        return True
    except Exception as e:  # noqa: BLE001 — the feedback is stored; the DM is a convenience
        log.error("store-tasks fail reason=feedback_notify store=%s id=%s err=%r", f["store"], f["id"], e)
        return False
