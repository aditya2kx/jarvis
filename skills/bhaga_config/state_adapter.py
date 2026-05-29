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
import json
import pathlib
import os
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
    "get_pending_otp",
    "save_pending_otp",
    "mark_otp_ready",
    "clear_pending_otp",
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


# ── Pending-OTP availability checkpoint ───────────────────────────────
#
# Records that a run reached an OTP gate and is waiting for the operator to
# reply READY (the two-step availability handshake). Lives in the SAME backend
# as the step markers so cloud=Firestore and local=disk transparently:
#   - local:     ~/.bhaga/state/run-<date>/pending_otp.json
#   - firestore: runs/<date> document, `pending_otp` map field
#
# Shape:
#   {
#     "portals":        ["Square", "ADP"],   # OTP portals this run still needs
#     "agent":          "bhaga",             # which Slack identity owns the DM
#     "requested_at":   "<CT ISO ts>",       # when the READY request was posted
#     "ready_received": false,               # set True by the resumer/webhook
#     "ready_at":       null,                # when READY arrived
#   }
#
# The cloud Slack webhook (cloud/webhook/handler.py) does NOT import this module
# (it's a standalone deploy unit), so it re-implements the same Firestore
# read/write. Keep the field names in sync if you change them here.

_PENDING_OTP_FILE = "pending_otp.json"


def _local_pending_otp_path(refresh_date: datetime.date) -> pathlib.Path:
    return (
        pathlib.Path.home() / ".bhaga" / "state"
        / f"run-{refresh_date.isoformat()}" / _PENDING_OTP_FILE
    )


def get_pending_otp(refresh_date: datetime.date) -> dict | None:
    """Return the pending-OTP checkpoint for refresh_date, or None if absent."""
    if _state_backend() == "firestore":
        client = _get_firestore_client()
        doc = _doc_ref(client, refresh_date).get()
        if not doc.exists:
            return None
        pending = (doc.to_dict() or {}).get("pending_otp")
        return pending or None

    path = _local_pending_otp_path(refresh_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_pending_otp(
    refresh_date: datetime.date,
    portals: list[str],
    *,
    requested_at: str,
    agent: str = "bhaga",
) -> dict:
    """Persist a fresh pending-OTP checkpoint (ready_received=False)."""
    payload = {
        "portals": list(portals),
        "agent": agent,
        "requested_at": requested_at,
        "ready_received": False,
        "ready_at": None,
    }
    if _state_backend() == "firestore":
        client = _get_firestore_client()
        _doc_ref(client, refresh_date).set({"pending_otp": payload}, merge=True)
        return payload

    path = _local_pending_otp_path(refresh_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return payload


def mark_otp_ready(
    refresh_date: datetime.date, *, ready_at: str | None = None
) -> bool:
    """Mark the pending checkpoint as READY-received. No-op if none pending.

    Returns True if a checkpoint was updated, False if nothing was pending.
    """
    pending = get_pending_otp(refresh_date)
    if pending is None:
        return False
    pending["ready_received"] = True
    pending["ready_at"] = ready_at or datetime.datetime.now(CT).isoformat()

    if _state_backend() == "firestore":
        client = _get_firestore_client()
        _doc_ref(client, refresh_date).set({"pending_otp": pending}, merge=True)
    else:
        path = _local_pending_otp_path(refresh_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pending, indent=2))
    return True


def clear_pending_otp(refresh_date: datetime.date) -> None:
    """Remove the pending-OTP checkpoint (idempotent)."""
    if _state_backend() == "firestore":
        client = _get_firestore_client()
        # Setting to None (rather than DELETE_FIELD) keeps the mock-friendly
        # merge semantics; get_pending_otp treats a falsy value as absent.
        _doc_ref(client, refresh_date).set({"pending_otp": None}, merge=True)
        return
    path = _local_pending_otp_path(refresh_date)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
