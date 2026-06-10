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
    "get_pipeline_halt",
    "set_pipeline_halt",
    "clear_pipeline_halt",
    "try_acquire_lock",
    "release_lock",
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


# ── Pipeline-halt circuit breaker ─────────────────────────────────────
#
# A GLOBAL (not per-refresh_date) flag that trips when a run produces
# semantically-bad output (e.g. the latest closed period's adp_paid is dead, or
# tips don't conserve). While tripped, daily_refresh refuses to start a fresh
# scheduled run so the same known-bad computation can't repeat night after
# night; a fully-healthy run auto-clears it. Mirrors the pending_otp checkpoint
# shape, but lives in a SINGLETON document (not keyed by date) since the breaker
# spans nights. Stored in the SAME (sandbox-isolated) collection as run state so
# a staging run can never trip/clear the prod breaker.
#   - local:     ~/.bhaga/state/pipeline_state.json
#   - firestore: <collection>/_pipeline_state document
#
# Shape: {halted: bool, reason: str|None, since: <CT ISO>|None,
#         refresh_date: <ISO>|None  # the run whose output tripped it}

_PIPELINE_STATE_FILE = "pipeline_state.json"
_PIPELINE_STATE_DOC = "_pipeline_state"


def _local_pipeline_state_path() -> pathlib.Path:
    return pathlib.Path.home() / ".bhaga" / "state" / _PIPELINE_STATE_FILE


def _pipeline_state_doc_ref(client):
    """Singleton run-state doc for the breaker (sandbox-isolated like run docs)."""
    collection = _collection_name()
    _assert_sandbox_state_isolation(collection)
    return client.collection(collection).document(_PIPELINE_STATE_DOC)


def get_pipeline_halt() -> dict | None:
    """Return the halt record if the pipeline is currently HALTED, else None."""
    if _state_backend() == "firestore":
        client = _get_firestore_client()
        doc = _pipeline_state_doc_ref(client).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        return data if data.get("halted") else None

    path = _local_pipeline_state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if data.get("halted") else None


def set_pipeline_halt(
    *,
    reason: str,
    refresh_date: datetime.date | None = None,
    since: str | None = None,
) -> dict:
    """Trip the breaker. Idempotent (a re-trip just refreshes reason/since)."""
    payload = {
        "halted": True,
        "reason": reason,
        "since": since or datetime.datetime.now(CT).isoformat(),
        "refresh_date": refresh_date.isoformat() if refresh_date else None,
    }
    if _state_backend() == "firestore":
        client = _get_firestore_client()
        # Full set (not merge): the breaker doc is single-purpose.
        _pipeline_state_doc_ref(client).set(payload)
        return payload

    path = _local_pipeline_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return payload


def clear_pipeline_halt() -> None:
    """Reset the breaker to a healthy (not-halted) state. Idempotent."""
    if _state_backend() == "firestore":
        client = _get_firestore_client()
        _pipeline_state_doc_ref(client).set(
            {"halted": False, "reason": None, "since": None, "refresh_date": None}
        )
        return
    path = _local_pipeline_state_path()
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


# ── Distributed scrape lock ────────────────────────────────────────────────────
#
# Cross-execution mutex that serializes the Square (and future portal) browser
# login + download + session-persist window across Cloud Run executions. Each
# Cloud Run task has its own /tmp, so the old PID-file lock was invisible across
# concurrent executions (the 6/9 regression root cause).
#
# Lock document shape:
#   {
#     "holder":       "<hostname>:<pid>",   # unique per Cloud Run execution
#     "acquired_at":  "<UTC ISO>",          # when this holder acquired the lock
#     "expires_at":   "<UTC ISO>",          # wall-clock TTL (stale-reclaim after crash)
#   }
#
# Backend mapping:
#   - firestore: <collection>/_lock_<name>  (singleton doc; sandbox-isolated)
#   - local:     ~/.bhaga/state/locks/<name>.lock  (JSON file; PID alive-check reclaim)
#
# Serialisation guarantee: `try_acquire_lock` uses a Firestore *transactional*
# read-then-write (mirrors the sandbox slot lease in sandbox_provision.py) so
# two concurrent executions cannot both see "no lock" and both succeed.


def _locks_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".bhaga" / "state" / "locks"


def _local_lock_path(name: str) -> pathlib.Path:
    return _locks_dir() / f"{name}.lock"


def _lock_doc_id(name: str) -> str:
    return f"_lock_{name}"


def _lock_doc_ref(client, name: str):
    """Firestore document reference for a named lock (sandbox-isolated)."""
    collection = _collection_name()
    _assert_sandbox_state_isolation(collection)
    return client.collection(collection).document(_lock_doc_id(name))


def try_acquire_lock(
    name: str,
    *,
    holder: str,
    ttl_s: int = 3600,
) -> bool:
    """Attempt to acquire a distributed lock. Returns True on success, False if held.

    The lock is TTL-based: a crashed holder's lock is auto-reclaimed after
    ``ttl_s`` seconds (default 1 h, which is > the 30-min OTP wait). Callers
    must release the lock explicitly via ``release_lock`` in a finally block.

    For the Firestore backend the acquire is transactional — two concurrent
    Cloud Run executions cannot both succeed. For the local backend, the lock
    file is reclaimed if the holder's PID is no longer alive OR the TTL has
    elapsed (preserving laptop single-process semantics).

    Args:
        name:   logical lock name, e.g. ``"scrape-square-palmetto"``.
        holder: unique identifier for this execution, e.g. ``"<hostname>:<pid>"``.
        ttl_s:  lock time-to-live in seconds (used for stale-reclaim).

    Returns:
        True  — lock acquired; caller holds it.
        False — lock is held by another holder; caller must NOT proceed.
    """
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    expires_at = (now_utc + datetime.timedelta(seconds=ttl_s)).isoformat()
    acquired_at = now_utc.isoformat()

    if _state_backend() == "firestore":
        if _firestore is None:
            raise ImportError(
                "google-cloud-firestore is not installed but BHAGA_STATE_BACKEND=firestore"
            )
        client = _get_firestore_client()
        ref = _lock_doc_ref(client, name)
        transaction = client.transaction()

        @_firestore.transactional
        def _txn(txn):
            snap = ref.get(transaction=txn)
            if snap.exists:
                data = snap.to_dict() or {}
                existing_expires = data.get("expires_at", "")
                # Reclaim if expired
                if existing_expires and existing_expires > now_utc.isoformat():
                    # Still valid — someone else holds it
                    return False
            # Free slot (absent or expired): take it
            txn.set(ref, {
                "holder": holder,
                "acquired_at": acquired_at,
                "expires_at": expires_at,
            })
            return True

        return _txn(transaction)

    # ── Local filesystem backend ────────────────────────────────────────
    import socket as _socket

    lock_path = _local_lock_path(name)
    _locks_dir().mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            data = json.loads(lock_path.read_text())
            existing_expires = data.get("expires_at", "")
            existing_holder = str(data.get("holder", ""))
            # Reclaim if TTL elapsed (primary expiry mechanism).
            if existing_expires and existing_expires <= now_utc.isoformat():
                pass  # expired — fall through to acquire
            else:
                # Secondary: if the holder is on THIS host, also check PID liveness
                # so a crashed execution releases the lock before the TTL.
                # For a holder on a different hostname we cannot check liveness —
                # rely on TTL only.
                parts = existing_holder.rsplit(":", 1)
                existing_hostname = parts[0] if len(parts) == 2 else ""
                if existing_hostname == _socket.gethostname():
                    try:
                        pid = int(parts[1])
                        os.kill(pid, 0)
                        # PID alive on this host — lock is genuinely held
                        return False
                    except (ValueError, OSError, ProcessLookupError):
                        pass  # dead PID — stale lock, reclaim
                else:
                    # Remote hostname: can't check PID; within TTL → held
                    return False
        except (json.JSONDecodeError, OSError):
            pass  # unreadable lock — reclaim

    lock_path.write_text(json.dumps({
        "holder": holder,
        "acquired_at": acquired_at,
        "expires_at": expires_at,
    }))
    return True


def release_lock(name: str, *, holder: str) -> bool:
    """Release a distributed lock. Only releases if the caller is the current holder.

    Idempotent and safe to call from finally blocks. Returns True if the lock
    was owned by this holder and removed; False otherwise (already released or
    held by someone else — no-op).
    """
    if _state_backend() == "firestore":
        if _firestore is None:
            return False
        try:
            client = _get_firestore_client()
            ref = _lock_doc_ref(client, name)
            doc = ref.get()
            if not doc.exists:
                return False
            data = doc.to_dict() or {}
            if data.get("holder") != holder:
                return False
            ref.delete()
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[state_adapter] WARN: release_lock({name}) failed: {exc}")
            return False

    # ── Local filesystem backend ────────────────────────────────────────
    lock_path = _local_lock_path(name)
    try:
        if not lock_path.exists():
            return False
        data = json.loads(lock_path.read_text())
        if data.get("holder") != holder:
            return False
        lock_path.unlink(missing_ok=True)
        return True
    except (json.JSONDecodeError, OSError, Exception) as exc:  # noqa: BLE001
        print(f"[state_adapter] WARN: release_lock({name}) local failed: {exc}")
        return False
