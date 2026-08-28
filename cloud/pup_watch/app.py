"""HTTP surface for pup-watch.

Unlike tesla-aladdin-garage this service does **not** run a background thread or
hold a warm instance. Cloud Scheduler POSTs /tick once a minute; when no
monitoring session is open the handler returns in about a millisecond. That is
what keeps the whole thing inside the Cloud Run free tier, and it is only
possible because a 60s notification delay is acceptable here.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from flask import Flask, jsonify, request

from . import persist, worker
from .config import load_cameras, notify_recipients, settings_with_overlay

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pup_watch")

app = Flask(__name__)

TOKEN_HEADER = "X-PupWatch-Token"


def _authorised(req: Any) -> bool:
    expected = os.environ.get("PUPWATCH_ADMIN_TOKEN", "").strip()
    if not expected:
        # Refuse rather than fall open: an unauthenticated /session/start would
        # let anyone burn the free tier and email the family.
        log.error("pup-watch fail reason=admin_token_unset")
        return False
    supplied = (req.headers.get(TOKEN_HEADER) or "").strip()
    return bool(supplied) and supplied == expected


def _deny():
    return jsonify({"ok": False, "error": "unauthorized"}), 401


@app.get("/health")
def health():
    cameras = [c.name for c in load_cameras()]
    session = persist.load_session()
    return jsonify({
        "ok": True,
        "service": "pup-watch",
        "cameras": cameras,
        "monitoring": bool(session.get("active")),
        "recipients": len(notify_recipients()),
        "persist": os.environ.get("PUPWATCH_PERSIST", "0") == "1",
    })


@app.post("/tick")
def tick():
    if not _authorised(request):
        return _deny()
    result = worker.tick()
    return jsonify({"ok": True, **result})


@app.get("/session")
def session_status():
    if not _authorised(request):
        return _deny()
    now = time.time()
    settings = settings_with_overlay(persist.load_config())
    session = persist.load_session()
    active, why = worker.session_active(session, now=now, settings=settings)
    return jsonify({
        "ok": True,
        "active": active,
        "reason": why,
        "session": session,
        "state": persist.load_state(),
    })


@app.post("/session/start")
def session_start():
    if not _authorised(request):
        return _deny()
    payload = request.get_json(silent=True) or {}
    now = time.time()
    settings = settings_with_overlay(persist.load_config())
    try:
        hours = float(payload.get("hours") or settings.session_max_hours)
    except (TypeError, ValueError):
        hours = settings.session_max_hours
    hours = max(0.25, min(hours, settings.session_max_hours))
    cameras = payload.get("cameras")
    session = {
        "active": True,
        "started_ts": now,
        "started_by": str(payload.get("by") or "operator"),
        "stop_after_ts": now + hours * 3600,
        "cameras": [str(c) for c in cameras] if isinstance(cameras, list) and cameras else None,
        "stopped_ts": None,
    }
    persist.save_session(session)
    # Clear episode bookkeeping so a fresh session can notify immediately
    # instead of inheriting the previous outing's cooldown.
    persist.save_state({
        "episode_active": False,
        "episode_started_ts": None,
        "episode_ended_ts": None,
        "last_seen_ts": None,
        "last_notified_ts": None,
    })
    log.info("pup-watch session_started hours=%.2f cameras=%s", hours, session["cameras"])
    return jsonify({"ok": True, "session": session, "hours": hours})


@app.post("/session/stop")
def session_stop():
    if not _authorised(request):
        return _deny()
    now = time.time()
    persist.save_session({"active": False, "stopped_ts": now, "stopped_by": "operator"})
    log.info("pup-watch session_stopped")
    return jsonify({"ok": True, "active": False})


if __name__ == "__main__":  # pragma: no cover — local dev only
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
