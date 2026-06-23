"""Tests for the distributed scrape-lock primitives in state_adapter.

All tests use the local filesystem backend (BHAGA_STATE_BACKEND=local) with a
tmp_path fixture so they are hermetic, fast, and require no GCP credentials.
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _local_backend_in_tmpdir(tmp_path, monkeypatch):
    """Force local backend and redirect ~/.bhaga/state to a temp dir."""
    monkeypatch.setenv("BHAGA_STATE_BACKEND", "local")
    monkeypatch.setenv("BHAGA_FIRESTORE_COLLECTION", "runs")
    # Redirect HOME so all local state goes to tmp_path.
    monkeypatch.setenv("HOME", str(tmp_path))
    # Force re-import of state_adapter so it picks up the patched HOME.
    import importlib
    import skills.bhaga_config.state_adapter as sa
    importlib.reload(sa)
    yield
    importlib.reload(sa)  # restore for other tests


def _sa():
    import skills.bhaga_config.state_adapter as sa
    return sa


# ---------------------------------------------------------------------------
# try_acquire_lock — basic acquire
# ---------------------------------------------------------------------------

def test_acquire_succeeds_when_no_lock(tmp_path):
    sa = _sa()
    acquired = sa.try_acquire_lock("test-lock-a", holder="hostA:123", ttl_s=60)
    assert acquired is True


def test_acquire_second_holder_fails_while_held(tmp_path):
    sa = _sa()
    sa.try_acquire_lock("test-lock-b", holder="hostA:100", ttl_s=60)
    acquired = sa.try_acquire_lock("test-lock-b", holder="hostB:200", ttl_s=60)
    assert acquired is False


def test_acquire_same_holder_fails_while_held(tmp_path):
    """Even the same holder string cannot re-acquire (not reentrant by design)."""
    sa = _sa()
    sa.try_acquire_lock("test-lock-c", holder="hostA:100", ttl_s=60)
    acquired = sa.try_acquire_lock("test-lock-c", holder="hostA:100", ttl_s=60)
    assert acquired is False


# ---------------------------------------------------------------------------
# try_acquire_lock — stale reclaim
# ---------------------------------------------------------------------------

def test_expired_lock_is_reclaimable(tmp_path, monkeypatch):
    """A lock whose TTL has elapsed is automatically reclaimed."""
    sa = _sa()
    # Acquire with a 1-second TTL, then wind clock forward artificially.
    sa.try_acquire_lock("test-lock-d", holder="hostA:111", ttl_s=1)

    # Overwrite the lock file with an already-expired timestamp.
    lock_path = sa._local_lock_path("test-lock-d")
    expired_data = {
        "holder": "hostA:111",
        "acquired_at": "2020-01-01T00:00:00+00:00",
        "expires_at": "2020-01-01T00:00:01+00:00",  # far in the past
    }
    lock_path.write_text(json.dumps(expired_data))

    acquired = sa.try_acquire_lock("test-lock-d", holder="hostB:222", ttl_s=60)
    assert acquired is True


def test_dead_pid_lock_is_reclaimable(tmp_path):
    """A lock held by a dead PID on the SAME host is reclaimed via PID liveness check."""
    import datetime
    import socket

    sa = _sa()
    # Use the current hostname so the PID check path is exercised.
    dead_pid = 99999999
    current_host = socket.gethostname()
    future = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    ).isoformat()
    lock_path = sa._local_lock_path("test-lock-e")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({
        "holder": f"{current_host}:{dead_pid}",
        "acquired_at": "2026-06-10T00:00:00+00:00",
        "expires_at": future,
    }))

    # Should reclaim because os.kill(99999999, 0) raises ProcessLookupError
    acquired = sa.try_acquire_lock("test-lock-e", holder="newhost:456", ttl_s=60)
    assert acquired is True


def test_live_pid_lock_is_not_reclaimable(tmp_path):
    """A lock held by our own PID (simulating a live process) is not reclaimed."""
    sa = _sa()
    import datetime

    live_pid = os.getpid()  # definitely alive
    future = (
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    ).isoformat()
    lock_path = sa._local_lock_path("test-lock-f")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({
        "holder": f"somehost:{live_pid}",
        "acquired_at": "2026-06-10T00:00:00+00:00",
        "expires_at": future,
    }))

    acquired = sa.try_acquire_lock("test-lock-f", holder="otherhost:789", ttl_s=60)
    assert acquired is False


# ---------------------------------------------------------------------------
# release_lock
# ---------------------------------------------------------------------------

def test_release_by_owner_succeeds(tmp_path):
    sa = _sa()
    sa.try_acquire_lock("test-lock-g", holder="hostA:10", ttl_s=60)
    released = sa.release_lock("test-lock-g", holder="hostA:10")
    assert released is True


def test_release_by_non_owner_is_noop(tmp_path):
    sa = _sa()
    sa.try_acquire_lock("test-lock-h", holder="hostA:10", ttl_s=60)
    released = sa.release_lock("test-lock-h", holder="hostB:20")
    assert released is False
    # Lock is still held by hostA
    assert sa._local_lock_path("test-lock-h").exists()


def test_release_absent_lock_is_noop(tmp_path):
    sa = _sa()
    released = sa.release_lock("test-lock-i", holder="hostA:10")
    assert released is False


def test_release_then_acquire_succeeds(tmp_path):
    sa = _sa()
    sa.try_acquire_lock("test-lock-j", holder="hostA:10", ttl_s=60)
    sa.release_lock("test-lock-j", holder="hostA:10")
    acquired = sa.try_acquire_lock("test-lock-j", holder="hostB:20", ttl_s=60)
    assert acquired is True


