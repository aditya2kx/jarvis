"""State adapter: local FS or Firestore backend for run markers.

CRITICAL CONTRACT (from commit 86e315a):
- Documents are keyed by refresh_date, NEVER by wall-clock at write time.
- A recovery run for 2026-05-21 writes to runs/2026-05-21, not runs/<today>.
- is_refresh_date_complete() must check the SAME set of required steps.
- The hard-refuse gate (no writes for today before 21:00 CT or future dates)
  is enforced in daily_refresh.py, not here.

Backend selection (env var BHAGA_STATE_BACKEND):
  - "local" (default): filesystem at ~/.bhaga/state/run-<date>/
  - "firestore": Google Cloud Firestore collection `runs`
"""

from __future__ import annotations

import datetime
import os
import pathlib
from zoneinfo import ZoneInfo

try:
    from google.cloud import firestore as _firestore
except ImportError:
    _firestore = None

__all__ = [
    "run_state_dir",
    "step_already_done",
    "mark_step_done",
    "is_refresh_date_complete",
]

CT = ZoneInfo("America/Chicago")

_FIRESTORE_COLLECTION = "runs"


def _state_backend() -> str:
    return os.environ.get("BHAGA_STATE_BACKEND", "local").lower()


def _firestore_db_id() -> str:
    return os.environ.get("BHAGA_FIRESTORE_DB", "(default)")


def _get_firestore_client():
    if _firestore is None:
        raise ImportError(
            "google-cloud-firestore is not installed. "
            "Install it with: pip install google-cloud-firestore"
        )
    db_id = _firestore_db_id()
    if db_id == "(default)":
        return _firestore.Client()
    return _firestore.Client(database=db_id)


def _doc_ref(client, refresh_date: datetime.date):
    """Return Firestore document reference keyed by refresh_date ISO string."""
    return client.collection(_FIRESTORE_COLLECTION).document(refresh_date.isoformat())


# ── Public API ────────────────────────────────────────────────────────


def run_state_dir(refresh_date: datetime.date) -> pathlib.Path:
    """Return the local filesystem state directory for a given refresh_date.

    Only meaningful for the 'local' backend. For 'firestore', returns
    a virtual path (useful for logging/display but not for direct I/O).
    """
    if _state_backend() == "firestore":
        return pathlib.Path(f"firestore://{_FIRESTORE_COLLECTION}/{refresh_date.isoformat()}")
    return pathlib.Path.home() / ".bhaga" / "state" / f"run-{refresh_date.isoformat()}"


def step_already_done(refresh_date: datetime.date, step: str) -> bool:
    """Check if a step has been marked done for the given refresh_date."""
    backend = _state_backend()

    if backend == "firestore":
        client = _get_firestore_client()
        doc = _doc_ref(client, refresh_date).get()
        if not doc.exists:
            return False
        return step in (doc.to_dict() or {})

    # Local filesystem backend
    marker = (
        pathlib.Path.home() / ".bhaga" / "state"
        / f"run-{refresh_date.isoformat()}" / f"{step}.done"
    )
    return marker.exists()


def mark_step_done(
    refresh_date: datetime.date,
    step: str,
    *,
    note: str = "",
) -> None:
    """Mark a step as completed for the given refresh_date.

    CRITICAL: Documents/markers are keyed by refresh_date, never by
    wall-clock time. The done_at timestamp records WHEN the step ran,
    but the key is always the business date the data belongs to.
    """
    backend = _state_backend()
    done_at = datetime.datetime.now(CT).isoformat()

    if backend == "firestore":
        client = _get_firestore_client()
        doc_ref = _doc_ref(client, refresh_date)
        doc_ref.set({step: done_at}, merge=True)
        return

    # Local filesystem backend
    state_dir = pathlib.Path.home() / ".bhaga" / "state" / f"run-{refresh_date.isoformat()}"
    state_dir.mkdir(parents=True, exist_ok=True)
    body = done_at
    if note:
        body += f"\nnote: {note}"
    (state_dir / f"{step}.done").write_text(body)


def is_refresh_date_complete(
    refresh_date: datetime.date,
    required_steps: list[str],
) -> bool:
    """Return True iff all required steps are marked done for refresh_date."""
    backend = _state_backend()

    if backend == "firestore":
        client = _get_firestore_client()
        doc = _doc_ref(client, refresh_date).get()
        if not doc.exists:
            return False
        data = doc.to_dict() or {}
        return all(step in data for step in required_steps)

    # Local filesystem backend
    state_dir = pathlib.Path.home() / ".bhaga" / "state" / f"run-{refresh_date.isoformat()}"
    if not state_dir.is_dir():
        return False
    return all((state_dir / f"{step}.done").exists() for step in required_steps)
