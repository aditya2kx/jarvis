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

from . import persist, sessions, worker
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
    cameras = payload.get("cameras")
    result = sessions.start(
        hours=payload.get("hours"),
        by=str(payload.get("by") or "operator"),
        cameras=[str(c) for c in cameras] if isinstance(cameras, list) and cameras else None,
        settings=settings,
        now=now,
    )
    return jsonify({"ok": True, **result})


@app.post("/session/stop")
def session_stop():
    if not _authorised(request):
        return _deny()
    return jsonify({"ok": True, **sessions.stop(by="operator", now=time.time())})


if __name__ == "__main__":  # pragma: no cover — local dev only
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
