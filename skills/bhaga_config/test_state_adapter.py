"""Tests for skills.bhaga_config.state_adapter — local + firestore backends."""

from __future__ import annotations

import datetime
import os
from unittest.mock import MagicMock, patch

import pytest

from skills.bhaga_config import state_adapter


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Ensure each test starts with a clean env (local backend)."""
    monkeypatch.delenv("BHAGA_STATE_BACKEND", raising=False)
    monkeypatch.delenv("BHAGA_FIRESTORE_DB", raising=False)


# ── Local backend tests ───────────────────────────────────────────────


class TestLocalBackend:
    def test_run_state_dir_uses_refresh_date(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 25)
        result = state_adapter.run_state_dir(d)
        assert result == tmp_path / ".bhaga" / "state" / "run-2026-05-25"

    def test_step_already_done_false_when_no_marker(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 25)
        assert state_adapter.step_already_done(d, "square_transactions") is False

    def test_mark_then_check(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 25)
        state_adapter.mark_step_done(d, "square_transactions", note="test")
        assert state_adapter.step_already_done(d, "square_transactions") is True
        assert state_adapter.step_already_done(d, "adp_timecard") is False

    def test_is_refresh_date_complete_partial(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 25)
        required = ["square_transactions", "adp_timecard", "write_raw_sheets"]
        state_adapter.mark_step_done(d, "square_transactions")
        state_adapter.mark_step_done(d, "adp_timecard")
        assert state_adapter.is_refresh_date_complete(d, required) is False

    def test_is_refresh_date_complete_full(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 25)
        required = ["square_transactions", "adp_timecard"]
        state_adapter.mark_step_done(d, "square_transactions")
        state_adapter.mark_step_done(d, "adp_timecard")
        assert state_adapter.is_refresh_date_complete(d, required) is True

    def test_refresh_date_keying_never_uses_today(self, tmp_path, monkeypatch):
        """CRITICAL: markers are keyed by refresh_date, not by wall-clock."""
        monkeypatch.setenv("HOME", str(tmp_path))
        past_date = datetime.date(2026, 5, 20)
        today = datetime.date(2026, 5, 26)

        state_adapter.mark_step_done(past_date, "some_step")

        # Marker lives under the past_date dir, NOT today
        past_marker = tmp_path / ".bhaga" / "state" / "run-2026-05-20" / "some_step.done"
        today_marker = tmp_path / ".bhaga" / "state" / "run-2026-05-26" / "some_step.done"
        assert past_marker.exists()
        assert not today_marker.exists()

        # Lookup also uses refresh_date
        assert state_adapter.step_already_done(past_date, "some_step") is True
        assert state_adapter.step_already_done(today, "some_step") is False


# ── Pending-OTP checkpoint (local backend) ────────────────────────────


class TestPendingOtpLocal:
    def test_absent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 28)
        assert state_adapter.get_pending_otp(d) is None

    def test_save_then_get(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 28)
        state_adapter.save_pending_otp(
            d, ["Square", "ADP"], requested_at="2026-05-28T21:00:00-05:00",
            agent="bhaga",
        )
        pending = state_adapter.get_pending_otp(d)
        assert pending["portals"] == ["Square", "ADP"]
        assert pending["agent"] == "bhaga"
        assert pending["ready_received"] is False
        # Marker file lives under the refresh_date run dir.
        assert (tmp_path / ".bhaga" / "state" / "run-2026-05-28" / "pending_otp.json").exists()

    def test_mark_ready(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 28)
        state_adapter.save_pending_otp(d, ["Square"], requested_at="2026-05-28T21:00:00-05:00")
        assert state_adapter.mark_otp_ready(d) is True
        pending = state_adapter.get_pending_otp(d)
        assert pending["ready_received"] is True
        assert pending["ready_at"]

    def test_mark_ready_no_checkpoint_is_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 28)
        assert state_adapter.mark_otp_ready(d) is False

    def test_clear(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        d = datetime.date(2026, 5, 28)
        state_adapter.save_pending_otp(d, ["Square"], requested_at="2026-05-28T21:00:00-05:00")
        state_adapter.clear_pending_otp(d)
        assert state_adapter.get_pending_otp(d) is None
        # Idempotent.
        state_adapter.clear_pending_otp(d)


# ── Firestore backend tests ───────────────────────────────────────────


class TestFirestoreBackend:
    @pytest.fixture(autouse=True)
    def _use_firestore(self, monkeypatch):
        monkeypatch.setenv("BHAGA_STATE_BACKEND", "firestore")

    def _mock_client(self):
        """Create a mock Firestore client with in-memory doc storage."""
        client = MagicMock()
        self._docs: dict[str, dict] = {}

        def _collection(name):
            col = MagicMock()

            def _document(doc_id):
                doc_ref = MagicMock()

                def _get():
                    snapshot = MagicMock()
                    snapshot.exists = doc_id in self._docs
                    snapshot.to_dict = lambda: self._docs.get(doc_id, {}).copy()
                    return snapshot

                def _set(data, merge=False):
                    if merge and doc_id in self._docs:
                        self._docs[doc_id].update(data)
                    else:
                        self._docs[doc_id] = data.copy()

                doc_ref.get = _get
                doc_ref.set = _set
                return doc_ref

            col.document = _document
            return col

        client.collection = _collection
        return client

    def test_step_already_done_false_initially(self):
        mock_client = self._mock_client()
        with patch.object(state_adapter, "_get_firestore_client", return_value=mock_client):
            d = datetime.date(2026, 5, 25)
            assert state_adapter.step_already_done(d, "square_transactions") is False

    def test_mark_then_check(self):
        mock_client = self._mock_client()
        with patch.object(state_adapter, "_get_firestore_client", return_value=mock_client):
            d = datetime.date(2026, 5, 25)
            state_adapter.mark_step_done(d, "square_transactions")
            assert state_adapter.step_already_done(d, "square_transactions") is True
            assert state_adapter.step_already_done(d, "adp_timecard") is False

    def test_is_refresh_date_complete(self):
        mock_client = self._mock_client()
        with patch.object(state_adapter, "_get_firestore_client", return_value=mock_client):
            d = datetime.date(2026, 5, 25)
            required = ["square_transactions", "adp_timecard"]
            state_adapter.mark_step_done(d, "square_transactions")
            assert state_adapter.is_refresh_date_complete(d, required) is False
            state_adapter.mark_step_done(d, "adp_timecard")
            assert state_adapter.is_refresh_date_complete(d, required) is True

    def test_refresh_date_keying_uses_date_not_today(self):
        """CRITICAL: Firestore docs keyed by refresh_date, not wall-clock."""
        mock_client = self._mock_client()
        with patch.object(state_adapter, "_get_firestore_client", return_value=mock_client):
            past = datetime.date(2026, 5, 20)
            today = datetime.date(2026, 5, 26)
            state_adapter.mark_step_done(past, "some_step")

            assert state_adapter.step_already_done(past, "some_step") is True
            assert state_adapter.step_already_done(today, "some_step") is False
            # Verify the doc is stored under the past date key
            assert "2026-05-20" in self._docs
            assert "2026-05-26" not in self._docs

    def test_run_state_dir_returns_virtual_path(self):
        d = datetime.date(2026, 5, 25)
        result = state_adapter.run_state_dir(d)
        assert "firestore" in str(result)
        assert "2026-05-25" in str(result)

    def test_pending_otp_save_get_ready_clear(self):
        mock_client = self._mock_client()
        with patch.object(state_adapter, "_get_firestore_client", return_value=mock_client):
            d = datetime.date(2026, 5, 28)
            assert state_adapter.get_pending_otp(d) is None
            state_adapter.save_pending_otp(
                d, ["Square", "ADP"], requested_at="2026-05-28T21:00:00-05:00",
            )
            pending = state_adapter.get_pending_otp(d)
            assert pending["portals"] == ["Square", "ADP"]
            assert pending["ready_received"] is False
            # Mark ready (cloud webhook half of the handshake does this too).
            assert state_adapter.mark_otp_ready(d) is True
            assert state_adapter.get_pending_otp(d)["ready_received"] is True
            # Clearing leaves the run doc but drops the checkpoint.
            state_adapter.clear_pending_otp(d)
            assert state_adapter.get_pending_otp(d) is None

    def test_pending_otp_coexists_with_step_markers(self):
        """pending_otp and step markers share the runs/<date> doc cleanly."""
        mock_client = self._mock_client()
        with patch.object(state_adapter, "_get_firestore_client", return_value=mock_client):
            d = datetime.date(2026, 5, 28)
            state_adapter.mark_step_done(d, "square_transactions")
            state_adapter.save_pending_otp(d, ["ADP"], requested_at="2026-05-28T21:00:00-05:00")
            # Step marker survives the pending write (merge semantics).
            assert state_adapter.step_already_done(d, "square_transactions") is True
            assert state_adapter.get_pending_otp(d)["portals"] == ["ADP"]


# ── Backend-agnostic interface tests ──────────────────────────────────


class TestBackendAgnostic:
    """Verify both backends expose the same interface contract."""

    @pytest.mark.parametrize("backend", ["local", "firestore"])
    def test_interface_consistency(self, backend, tmp_path, monkeypatch):
        monkeypatch.setenv("BHAGA_STATE_BACKEND", backend)
        monkeypatch.setenv("HOME", str(tmp_path))

        d = datetime.date(2026, 5, 25)

        if backend == "firestore":
            mock_client = MagicMock()
            docs: dict[str, dict] = {}

            def _collection(name):
                col = MagicMock()

                def _document(doc_id):
                    doc_ref = MagicMock()

                    def _get():
                        s = MagicMock()
                        s.exists = doc_id in docs
                        s.to_dict = lambda: docs.get(doc_id, {}).copy()
                        return s

                    def _set(data, merge=False):
                        if merge and doc_id in docs:
                            docs[doc_id].update(data)
                        else:
                            docs[doc_id] = data.copy()

                    doc_ref.get = _get
                    doc_ref.set = _set
                    return doc_ref

                col.document = _document
                return col

            mock_client.collection = _collection
            ctx = patch.object(state_adapter, "_get_firestore_client", return_value=mock_client)
        else:
            from contextlib import nullcontext
            ctx = nullcontext()

        with ctx:
            # All four functions should work without error
            assert state_adapter.step_already_done(d, "test_step") is False
            state_adapter.mark_step_done(d, "test_step")
            assert state_adapter.step_already_done(d, "test_step") is True
            assert state_adapter.is_refresh_date_complete(d, ["test_step"]) is True
            assert state_adapter.is_refresh_date_complete(d, ["test_step", "other"]) is False
            _ = state_adapter.run_state_dir(d)
