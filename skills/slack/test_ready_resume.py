#!/usr/bin/env python3
"""Tests for the non-cloud READY-handshake resumer.

Covers the laptop path: the daily run posts a READY request + a pending_otp
checkpoint and exits while the laptop is closed; the operator replies READY
from their phone; when the laptop wakes, the poll loop reads the reply from
the DM backlog and resumes the checkpointed run.

No subprocess is forked and no Slack DM is sent — `_trigger_resume`,
`mark_otp_ready`, and the DM sender are all mocked.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.slack import command_handler, poll_commands


def _write_pending(state_dir: pathlib.Path, date_iso: str, *, ready: bool):
    run_dir = state_dir / f"run-{date_iso}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "pending_otp.json").write_text(json.dumps({
        "portals": ["Square", "ADP"],
        "agent": "bhaga",
        "requested_at": f"{date_iso}T21:00:00-05:00",
        "ready_received": ready,
        "ready_at": None,
    }))
    return run_dir


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    sd = tmp_path / ".bhaga" / "state"
    sd.mkdir(parents=True)
    monkeypatch.setattr(command_handler, "STATE_DIR", sd)
    return sd


class TestFindPendingOtpDate:
    def test_none_when_no_state(self, state_dir):
        assert command_handler.find_pending_otp_date() is None

    def test_returns_newest_unready(self, state_dir):
        _write_pending(state_dir, "2026-05-26", ready=False)
        _write_pending(state_dir, "2026-05-28", ready=False)
        import datetime
        assert command_handler.find_pending_otp_date() == datetime.date(2026, 5, 28)

    def test_skips_already_ready(self, state_dir):
        _write_pending(state_dir, "2026-05-28", ready=True)
        assert command_handler.find_pending_otp_date() is None


class TestHandleReady:
    def test_ready_marks_and_resumes(self, state_dir):
        _write_pending(state_dir, "2026-05-28", ready=False)
        with mock.patch.object(command_handler, "_trigger_resume") as mock_resume, \
             mock.patch("skills.bhaga_config.state_adapter.mark_otp_ready") as mock_mark:
            ack = command_handler.handle_ready("ready")
        assert ack is not None
        assert "2026-05-28" in ack
        import datetime
        mock_mark.assert_called_once_with(datetime.date(2026, 5, 28))
        mock_resume.assert_called_once_with(datetime.date(2026, 5, 28))

    def test_lenient_word_resumes(self, state_dir):
        _write_pending(state_dir, "2026-05-28", ready=False)
        with mock.patch.object(command_handler, "_trigger_resume") as mock_resume, \
             mock.patch("skills.bhaga_config.state_adapter.mark_otp_ready"):
            assert command_handler.handle_ready("ok go") is not None
        mock_resume.assert_called_once()

    def test_no_pending_returns_none(self, state_dir):
        with mock.patch.object(command_handler, "_trigger_resume") as mock_resume:
            assert command_handler.handle_ready("ready") is None
        mock_resume.assert_not_called()

    def test_non_ready_text_returns_none(self, state_dir):
        _write_pending(state_dir, "2026-05-28", ready=False)
        with mock.patch.object(command_handler, "_trigger_resume") as mock_resume:
            assert command_handler.handle_ready("123456") is None
            assert command_handler.handle_ready("status") is None
        mock_resume.assert_not_called()


class TestPollOnceReadsBacklog:
    def test_backlog_ready_triggers_resume(self, state_dir, tmp_path, monkeypatch):
        """Simulated DM backlog: a READY reply newer than the cursor resumes."""
        _write_pending(state_dir, "2026-05-28", ready=False)
        last_ts = tmp_path / "last_command_ts.txt"
        monkeypatch.setattr(poll_commands, "STATE_DIR", state_dir)
        monkeypatch.setattr(poll_commands, "LAST_TS_FILE", last_ts)

        backlog = {
            "ok": True,
            "messages": [
                {"ts": "1700000100.0", "text": "READY", "user": "U_OP"},
            ],
        }
        with mock.patch("skills.slack.adapter._api_call", return_value=backlog), \
             mock.patch.object(poll_commands, "_send_dm") as mock_dm, \
             mock.patch.object(command_handler, "_trigger_resume") as mock_resume, \
             mock.patch("skills.bhaga_config.state_adapter.mark_otp_ready"):
            rc = poll_commands.poll_once()

        assert rc == 0
        mock_resume.assert_called_once()
        mock_dm.assert_called_once()  # the resume acknowledgement
        # Cursor advanced so the same backlog reply is never reprocessed.
        assert last_ts.read_text().strip() == "1700000100.0"

    def test_backlog_otp_code_does_not_resume(self, state_dir, tmp_path, monkeypatch):
        """A numeric code in the backlog is not a READY reply; no resume."""
        _write_pending(state_dir, "2026-05-28", ready=False)
        last_ts = tmp_path / "last_command_ts.txt"
        monkeypatch.setattr(poll_commands, "STATE_DIR", state_dir)
        monkeypatch.setattr(poll_commands, "LAST_TS_FILE", last_ts)

        backlog = {"ok": True, "messages": [
            {"ts": "1700000100.0", "text": "482913", "user": "U_OP"},
        ]}
        with mock.patch("skills.slack.adapter._api_call", return_value=backlog), \
             mock.patch.object(poll_commands, "_send_dm"), \
             mock.patch.object(command_handler, "_trigger_resume") as mock_resume, \
             mock.patch.object(command_handler, "_trigger_recovery") as mock_recovery:
            rc = poll_commands.poll_once()

        assert rc == 0
        mock_resume.assert_not_called()
        mock_recovery.assert_not_called()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
