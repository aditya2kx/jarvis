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

_client: Any = None


def _db():
    global _client
    if os.environ.get("GARAGE_PERSIST", "0") != "1":
        return None
    if _client is not None:
        return _client
    try:
        from google.cloud import firestore

        project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        _client = firestore.Client(project=project) if project else firestore.Client()
        return _client
    except Exception as e:  # noqa: BLE001 — persist is best-effort
        log.error("tesla-aladdin-garage fail reason=firestore_client err=%s", e)
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
        log.error("tesla-aladdin-garage fail reason=config_load err=%s", e)
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
        log.error("tesla-aladdin-garage fail reason=state_save err=%s", e)


def load_state() -> dict:
    db = _db()
    if db is None:
        return {}
    try:
        snap = db.collection(COLLECTION).document(STATE_DOC).get()
        return snap.to_dict() or {} if snap.exists else {}
    except Exception as e:  # noqa: BLE001
        log.error("tesla-aladdin-garage fail reason=state_load err=%s", e)
        return {}
