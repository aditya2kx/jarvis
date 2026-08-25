"""Firestore last-state + config overlay for tesla-aladdin-garage.

Survives Cloud Run instance death. Tests inject a fake client.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

log = logging.getLogger("tesla_aladdin_garage")

COLLECTION = "tesla_aladdin_garage"
CONFIG_DOC = "config"
STATE_DOC = "state"
USAGE_DOC = "tesla_usage"

# Process-local fallback when Firestore is off or failing (one Cloud Run instance).
_usage_mem: dict[str, Any] = {}

_client: Any = None


def _firestore_database() -> str:
    raw = (
        os.environ.get("GARAGE_FIRESTORE_DB")
        or os.environ.get("FIRESTORE_DB")
        or "(default)"
    )
    return raw.strip() or "(default)"


def _db():
    global _client
    if os.environ.get("GARAGE_PERSIST", "0") != "1":
        return None
    if _client is not None:
        return _client
    try:
        from google.cloud import firestore

        # Never pass database="(default)". REST transports URL-encode the id
        # once in the resource name and again in the path, so the API sees
        # "%28default%29" and returns 400 Invalid database id %28default%29.
        # Same pattern as skills.bhaga_config.state_adapter / bhaga-webhook.
        kwargs: dict[str, Any] = {}
        project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project:
            kwargs["project"] = project
        db_id = _firestore_database()
        if db_id != "(default)":
            kwargs["database"] = db_id
        _client = firestore.Client(**kwargs)
        return _client
    except Exception as e:  # noqa: BLE001 — persist is best-effort
        log.error("tesla-aladdin-garage fail reason=firestore_client err=%r", e)
        return None


def set_client(client: Any) -> None:
    """Tests: inject a fake Firestore client."""
    global _client
    _client = client


def load_config() -> dict:
    db = _db()
    if db is None:
        return {}
    try:
        snap = db.collection(COLLECTION).document(CONFIG_DOC).get()
        return snap.to_dict() or {} if snap.exists else {}
    except Exception as e:  # noqa: BLE001
        log.error("tesla-aladdin-garage fail reason=config_load err=%r", e)
        return {}


def save_config(overlay: dict) -> None:
    db = _db()
    if db is None:
        return
    payload = {k: v for k, v in overlay.items() if v is not None}
    payload["updated_ts"] = time.time()
    db.collection(COLLECTION).document(CONFIG_DOC).set(payload, merge=True)
    log.info("tesla-aladdin-garage config_saved keys=%s", sorted(payload))


def save_state(state: dict) -> None:
    db = _db()
    if db is None:
        return
    try:
        db.collection(COLLECTION).document(STATE_DOC).set(state, merge=True)
    except Exception as e:  # noqa: BLE001
        log.error("tesla-aladdin-garage fail reason=state_save err=%r", e)


def load_state() -> dict:
    db = _db()
    if db is None:
        return {}
    try:
        snap = db.collection(COLLECTION).document(STATE_DOC).get()
        return snap.to_dict() or {} if snap.exists else {}
    except Exception as e:  # noqa: BLE001
        log.error("tesla-aladdin-garage fail reason=state_load err=%r", e)
        return {}


def _empty_usage(month: str) -> dict[str, Any]:
    return {"month": month, "data": 0, "commands": 0, "wakes": 0, "signals": 0}


def load_tesla_usage(month: str) -> dict[str, Any]:
    cached = _usage_mem.get(month)
    db = _db()
    if db is None:
        return dict(cached or _empty_usage(month))
    try:
        snap = db.collection(COLLECTION).document(USAGE_DOC).get()
        data = snap.to_dict() or {} if snap.exists else {}
        if data.get("month") != month:
            return dict(cached or _empty_usage(month))
        _usage_mem[month] = data
        return dict(data)
    except Exception as e:  # noqa: BLE001
        log.error("tesla-aladdin-garage fail reason=usage_load err=%r", e)
        return dict(cached or _empty_usage(month))


def record_billable(category: str, n: int = 1) -> None:
    """Increment this UTC-month Tesla usage bucket. Categories: data, commands, wakes, signals."""
    from datetime import datetime, timezone

    if category not in ("data", "commands", "wakes", "signals"):
        category = "data"
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    cur = load_tesla_usage(month)
    if cur.get("month") != month:
        cur = _empty_usage(month)
    cur[category] = int(cur.get(category) or 0) + int(n)
    cur["month"] = month
    cur["updated_ts"] = time.time()
    _usage_mem[month] = cur
    db = _db()
    if db is None:
        return
    try:
        db.collection(COLLECTION).document(USAGE_DOC).set(cur, merge=True)
    except Exception as e:  # noqa: BLE001
        log.error("tesla-aladdin-garage fail reason=usage_save err=%r", e)
