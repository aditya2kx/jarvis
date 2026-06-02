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
    "clear_step",
    "is_refresh_date_complete",
    "get_pending_otp",
    "save_pending_otp",
    "mark_otp_ready",
    "clear_pending_otp",
    "record_step_failure",
]

CT = ZoneInfo("America/Chicago")

# The canonical PRODUCTION run-state collection. A sandbox/staging run must
# divert ALL run-state (markers, pending-OTP, failures) to its own collection so
# it never reads or writes prod run state — see .cursor/rules/bhaga-principles.md
# (sandbox isolation). Override with BHAGA_FIRESTORE_COLLECTION.
_PROD_FIRESTORE_COLLECTION = "runs"


def _state_backend() -> str:
    return os.environ.get("BHAGA_STATE_BACKEND", "local").lower()


def _firestore_db_id() -> str:
    return os.environ.get("BHAGA_FIRESTORE_DB", "(default)")


def _collection_name() -> str:
    """Firestore collection for run state. Defaults to the prod ``runs``; a
    sandbox/staging run sets BHAGA_FIRESTORE_COLLECTION to its own collection."""
    return os.environ.get("BHAGA_FIRESTORE_COLLECTION", _PROD_FIRESTORE_COLLECTION)


def _assert_sandbox_state_isolation(collection: str) -> None:
    """Hard guard: in staging/sandbox mode, block any use of the prod run-state
    collection. Mirrors the sheet + GCS guards — sandbox runs must never touch
    prod data sources (see .cursor/rules/bhaga-principles.md — sandbox isolation).
    """
    if os.environ.get("BHAGA_SHEET_MODE", "").lower() != "staging":
        return
    if collection == _PROD_FIRESTORE_COLLECTION:
        raise RuntimeError(
            f"BLOCKED: a sandbox/staging run targeted the production Firestore run-state "
            f"collection '{collection}'. Set BHAGA_FIRESTORE_COLLECTION to a sandbox "
            f"collection. Sandbox runs must never read or write prod run state "
            f"(see .cursor/rules/bhaga-principles.md — sandbox isolation)."
        )


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
    collection = _collection_name()
    _assert_sandbox_state_isolation(collection)
    return client.collection(collection).document(refresh_date.isoformat())


# ── Public API ────────────────────────────────────────────────────────


def run_state_dir(refresh_date: datetime.date) -> pathlib.Path:
    """Return the local filesystem state directory for a given refresh_date.

    Only meaningful for the 'local' backend. For 'firestore', returns
    a virtual path (useful for logging/display but not for direct I/O).
    """
    if _state_backend() == "firestore":
        return pathlib.Path(f"firestore://{_collection_name()}/{refresh_date.isoformat()}")
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


def clear_step(refresh_date: datetime.date, step: str) -> None:
    """Remove a step's done-marker for refresh_date (idempotent).

    Symmetric to mark_step_done. Used by the OTP-portal recovery path to
    invalidate stale DOWNSTREAM markers (write_raw_sheets / update_model_sheet /
    process_reviews) when a previously-failed portal succeeds on a later run, so
    those steps recompute on the now-complete data instead of being skipped.

    This is the sanctioned way to clear a marker (bhaga.md invariant: no ad-hoc
    `rm`/field-delete in a shell) — both backends honor it:
      - local:     unlink <step>.done (no-op if absent).
      - firestore: DELETE_FIELD removes the key so step_already_done — which
                   tests key PRESENCE — returns False. set(merge=True) is
                   idempotent even if the run doc doesn't exist yet.
    """
    backend = _state_backend()

    if backend == "firestore":
        client = _get_firestore_client()
        _doc_ref(client, refresh_date).set({step: _firestore.DELETE_FIELD}, merge=True)
        return

    # Local filesystem backend
    marker = (
        pathlib.Path.home() / ".bhaga" / "state"
        / f"run-{refresh_date.isoformat()}" / f"{step}.done"
    )
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass


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
    """Persist a fresh pending-OTP checkpoint (ready_received=False).

    The checkpoint self-describes its OTP routing (env / run_label / target_job)
    from the run's environment, so the webhook can resume the correct job — prod
    or a sandbox live run — even when both await OTP at once. Prod runs leave env
    'prod' and target_job '' (the webhook falls back to CLOUD_RUN_JOB_NAME), so
    behavior is unchanged for the nightly.
    """
    payload = {
        "portals": list(portals),
        "agent": agent,
        "requested_at": requested_at,
        "ready_received": False,
        "ready_at": None,
        "env": os.environ.get("BHAGA_RUN_ENV", "prod"),
        "run_label": os.environ.get("BHAGA_RUN_LABEL", ""),
        "target_job": os.environ.get("BHAGA_OTP_TARGET_JOB", ""),
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


def _local_failure_path(refresh_date: datetime.date, step: str) -> pathlib.Path:
    return (
        pathlib.Path.home() / ".bhaga" / "state"
        / f"run-{refresh_date.isoformat()}" / f"{step}.failure.json"
    )


def record_step_failure(
    refresh_date: datetime.date,
    step: str,
    *,
    error: str,
    evidence_uri: str | None = None,
    failed_at: str | None = None,
) -> None:
    """Record a per-step failure into the run state for postmortem-from-state.

    Captures the error class/message and the ``gs://`` evidence prefix so a future
    agent can diagnose the failure from Firestore + GCS + Cloud Run logs ALONE,
    without a rerun (see .cursor/rules/bhaga-principles.md — observability). Keyed
    by ``refresh_date`` like every other marker, NOT by wall-clock time.

    - firestore: ``runs/<date>`` document, ``failures.<step>`` map field
    - local:     ``~/.bhaga/state/run-<date>/<step>.failure.json``

    Best-effort: never raises. Observability must never mask the real exception.
    """
    payload = {
        "error": error,
        "evidence_uri": evidence_uri,
        "failed_at": failed_at or datetime.datetime.now(CT).isoformat(),
    }
    try:
        if _state_backend() == "firestore":
            client = _get_firestore_client()
            _doc_ref(client, refresh_date).set({"failures": {step: payload}}, merge=True)
            return
        path = _local_failure_path(refresh_date, step)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"[state_adapter] WARN: could not record step failure {step}: {exc}")
