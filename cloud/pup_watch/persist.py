"""Firestore session + episode state for pup-watch.

Mirrors cloud/tesla_aladdin_garage/persist.py: gated by an env flag, fail-open
on every path, and injectable for tests. State must outlive the instance
because each poll is a fresh Cloud Run request, so "have I already emailed
about this visit?" cannot live in memory.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

log = logging.getLogger("pup_watch")

COLLECTION = "pup_watch"
CONFIG_DOC = "config"
SESSION_DOC = "session"
STATE_DOC = "state"

_client: Any = None


def _firestore_database() -> str:
    raw = os.environ.get("PUPWATCH_FIRESTORE_DB") or "pupwatch"
    return raw.strip() or "pupwatch"


def _db():
    global _client
    if os.environ.get("PUPWATCH_PERSIST", "0") != "1":
        return None
    if _client is not None:
        return _client
    try:
        from google.cloud import firestore

        kwargs: dict[str, Any] = {}
        project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            kwargs["project"] = project
        db_id = _firestore_database()
        if db_id != "(default)":
            kwargs["database"] = db_id
        _client = firestore.Client(**kwargs)
        log.info("pup-watch firestore_client database=%r", db_id)
        return _client
    except Exception as e:  # noqa: BLE001 — persist is best-effort
        log.error("pup-watch fail reason=firestore_client err=%r", e)
        return None


def set_client(client: Any) -> None:
    """Tests: inject a fake Firestore client (or None to reset)."""
    global _client
    _client = client


def _load(doc: str) -> dict:
    db = _db()
    if db is None:
        return {}
    try:
        snap = db.collection(COLLECTION).document(doc).get()
        return (snap.to_dict() or {}) if snap.exists else {}
    except Exception as e:  # noqa: BLE001
        log.error("pup-watch fail reason=%s_load err=%r", doc, e)
        return {}


def _save(doc: str, payload: dict) -> None:
    db = _db()
    if db is None:
        return
    try:
        db.collection(COLLECTION).document(doc).set({**payload, "updated_ts": time.time()}, merge=True)
    except Exception as e:  # noqa: BLE001
        log.error("pup-watch fail reason=%s_save err=%r", doc, e)


def load_config() -> dict:
    return _load(CONFIG_DOC)


def save_config(overlay: dict) -> None:
    _save(CONFIG_DOC, {k: v for k, v in overlay.items() if v is not None})


def load_session() -> dict:
    return _load(SESSION_DOC)


def save_session(session: dict) -> None:
    _save(SESSION_DOC, session)


def load_state() -> dict:
    return _load(STATE_DOC)


def save_state(state: dict) -> None:
    _save(STATE_DOC, state)
