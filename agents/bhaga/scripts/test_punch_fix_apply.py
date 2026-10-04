"""Tests for punch_fix_apply — the console "Write to ADP" job (Issue #356)."""

from __future__ import annotations

import contextlib

import pytest

from agents.bhaga.scripts import punch_fix_apply as pfa


def test_refuses_without_writeback_flag(monkeypatch):
    monkeypatch.delenv(pfa.WRITEBACK_FLAG, raising=False)
    with pytest.raises(RuntimeError, match="BHAGA_PUNCH_FIX_WRITEBACK"):
        pfa.apply_decisions("palmetto", ["d1"])


def test_requires_decision_ids(monkeypatch):
    monkeypatch.setenv("BHAGA_PUNCH_FIX_DECISION_IDS", " ")
    with pytest.raises(RuntimeError, match="empty"):
        pfa.run_from_env("palmetto")


def test_login_failure_leaves_no_row_stuck_applying(monkeypatch):
    monkeypatch.setenv(pfa.WRITEBACK_FLAG, "1")
    monkeypatch.setattr(pfa, "get_client", lambda: object())
    monkeypatch.setattr(pfa, "_load", lambda c, s, ids: [{"decision_id": "d1"}])
    failed: list = []
    monkeypatch.setattr(pfa, "_fail_leftovers", lambda c, s, ids, err: failed.append((ids, err)))

    from skills.adp_run_automation import runner

    @contextlib.contextmanager
    def broken_session(**_):
        raise RuntimeError("ADP login needs a code")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "adp_session", broken_session)
    with pytest.raises(RuntimeError, match="needs a code"):
        pfa.apply_decisions("palmetto", ["d1"])
    assert failed == [(["d1"], "apply job ended before this entry was written")]


def test_expect_in_reads_the_open_entry():
    row = {"entries_json": '[{"in": "06:28", "out": "11:10"}, {"in": "11:30", "out": null}]',
           "open_entry_index": 1}
    assert pfa._expect_in(row) == "11:30"
    assert pfa._expect_in({**row, "open_entry_index": 5}) is None
