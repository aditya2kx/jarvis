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


CIMINO = {"decision_id": "d1", "date": "2026-10-04", "employee_id": "Denton, Cimino R",
          "in_time": "06:30", "out_time": "13:30", "kind": "no_entry"}
HILLARY = {"decision_id": "d2", "date": "2026-10-02", "employee_id": "Huynh, Hillary",
           "in_time": None, "out_time": "20:33", "kind": "missing_out"}


def _punch(row, in_time="06:00"):
    return {"date": row["date"], "employee_id": row["employee_id"],
            "in_time": row["in_time"] or in_time, "out_time": row["out_time"]}


def test_punch_in_export_matches_new_entry_on_in_and_out():
    assert pfa.punch_in_export(CIMINO, [_punch(CIMINO)])
    assert not pfa.punch_in_export(CIMINO, [{**_punch(CIMINO), "in_time": "07:00"}])
    assert not pfa.punch_in_export(CIMINO, [{**_punch(CIMINO), "date": "2026-10-03"}])


def test_punch_in_export_matches_clock_out_on_out_only():
    assert pfa.punch_in_export(HILLARY, [_punch(HILLARY, in_time="12:01")])
    assert not pfa.punch_in_export(HILLARY, [{**_punch(HILLARY), "out_time": "20:30"}])


class _Page:
    def __init__(self):
        self.url = "https://runpayroll.adp.com/v2/home"
        self.waits = 0

    def wait_for_timeout(self, _ms):
        self.waits += 1

    def goto(self, *_a, **_k):
        pass


def _fake_exports(monkeypatch, exports: list[list[dict]]):
    """Each Timecard download returns the next export's punches."""
    from skills.adp_run_automation import runner, shift_backend

    seq = iter(exports)
    downloads: list = []
    ui_json: list = []
    monkeypatch.setattr(runner, "_timecard_within_session",
                        lambda page, target_date, store: downloads.append(target_date) or "tc.xlsx")
    monkeypatch.setattr(runner, "_write_target_meta", lambda path, d: None)
    monkeypatch.setattr(shift_backend, "parse_xlsx", lambda path, employee_aliases=None: next(seq))
    monkeypatch.setattr(runner, "_timecard_gaps_within_session", lambda page: ["gaps"])
    monkeypatch.setattr(runner, "_write_timecards_ui_json",
                        lambda gaps, store: ui_json.append(gaps))
    monkeypatch.setattr("skills.store_profile.load_aliases", lambda store: {})
    monkeypatch.setenv("BHAGA_PUNCH_FIX_RESYNC_WAIT_S", "0")
    return downloads, ui_json


def test_resync_happy_path_one_download(monkeypatch):
    import datetime

    downloads, ui_json = _fake_exports(monkeypatch, [[_punch(CIMINO), _punch(HILLARY)]])
    missing = pfa._resync_in_session(_Page(), "u", "palmetto", [CIMINO, HILLARY],
                                     datetime.date(2026, 10, 4))
    assert missing == []
    assert downloads == [datetime.date(2026, 10, 4)]
    assert ui_json == [["gaps"]]


def test_resync_recovery_second_download_catches_lagging_export(monkeypatch):
    import datetime

    downloads, _ = _fake_exports(monkeypatch, [[_punch(HILLARY)], [_punch(CIMINO), _punch(HILLARY)]])
    missing = pfa._resync_in_session(_Page(), "u", "palmetto", [CIMINO, HILLARY],
                                     datetime.date(2026, 10, 4))
    assert missing == []
    assert len(downloads) == 2


def test_resync_failure_returns_rows_missing_after_three_downloads(monkeypatch):
    import datetime

    downloads, ui_json = _fake_exports(monkeypatch, [[_punch(HILLARY)]] * 3)
    missing = pfa._resync_in_session(_Page(), "u", "palmetto", [CIMINO, HILLARY],
                                     datetime.date(2026, 10, 4))
    assert missing == [CIMINO]
    assert len(downloads) == pfa.RESYNC_ATTEMPTS
    assert ui_json == [["gaps"]]


def _fake_write_session(monkeypatch, resync):
    """One login in which every write lands; ``resync`` stands in for the re-download."""
    import datetime
    import types

    from skills.adp_run_automation import runner
    from skills.adp_run_automation import timecard_fix_backend as tfb
    from skills.adp_run_automation import timecard_ui_backend as tub

    monkeypatch.setenv(pfa.WRITEBACK_FLAG, "1")
    monkeypatch.setattr(pfa, "get_client", lambda: object())
    rows = [{**CIMINO, "action": "accept", "open_entry_index": None, "decided_by": "op",
             "raw_employee_name": "Denton, Cimino R", "entries_json": "[]", "rule": "r"}]
    monkeypatch.setattr(pfa, "_load", lambda c, s, ids: rows)
    statuses: list = []
    monkeypatch.setattr(pfa, "_set_status", lambda c, i, st, err: statuses.append((i, st, err)))
    monkeypatch.setattr(pfa, "_fail_leftovers", lambda *a: None)

    @contextlib.contextmanager
    def session(**_):
        yield None, _Page()

    month = types.SimpleNamespace(employee_name="x", pay_period_start=datetime.date(2026, 9, 21),
                                  pay_period_end=datetime.date(2026, 10, 4))
    monkeypatch.setattr(runner, "adp_session", session)
    monkeypatch.setattr(runner, "_open_timecards", lambda page: "html")
    monkeypatch.setattr(tub, "parse_month_view", lambda html: month)
    monkeypatch.setattr(tfb, "PlaywrightTimecardUi",
                        lambda page, evidence_dir: types.SimpleNamespace(
                            select_employee=lambda name, cur: "html"))
    monkeypatch.setattr(tfb, "apply_new_entry", lambda ui, **k: types.SimpleNamespace(
        status=tfb.APPLIED, error=None, evidence_path=None))
    monkeypatch.setattr(pfa, "_resync_in_session", resync)
    return statuses


def test_written_punch_missing_from_export_ends_not_in_hours(monkeypatch, capsys):
    import datetime

    statuses = _fake_write_session(monkeypatch, lambda page, url, store, applied, d: applied)
    counts = pfa.apply_decisions("palmetto", ["d1"])
    assert counts["applied"] == 0 and counts[pfa.NOT_IN_HOURS] == 1
    assert counts["max_date"] == datetime.date(2026, 10, 4)
    assert statuses[-1][:2] == ("d1", pfa.NOT_IN_HOURS)
    assert "[punch-fix] NOT_IN_HOURS date=2026-10-04" in capsys.readouterr().out


def test_written_punch_in_export_stays_applied(monkeypatch):
    statuses = _fake_write_session(monkeypatch, lambda page, url, store, applied, d: [])
    counts = pfa.apply_decisions("palmetto", ["d1"])
    assert counts["applied"] == 1 and counts[pfa.NOT_IN_HOURS] == 0
    assert statuses == [("d1", "applied", None)]


def test_export_download_failure_leaves_write_unverified(monkeypatch):
    def boom(*_a):
        raise TimeoutError("export")

    statuses = _fake_write_session(monkeypatch, boom)
    counts = pfa.apply_decisions("palmetto", ["d1"])
    assert counts[pfa.NOT_IN_HOURS] == 1
    assert statuses[-1][1] == pfa.NOT_IN_HOURS and "TimeoutError" in statuses[-1][2]


class _Job:
    def __init__(self, rows=(), affected=0):
        self._rows, self.num_dml_affected_rows = rows, affected

    def result(self):
        return self._rows


class _Client:
    def __init__(self, missing):
        self.missing, self.sql = missing, []

    def query(self, sql, job_config=None):
        self.sql.append((sql, job_config))
        if sql.lstrip().startswith("SELECT"):
            return _Job(rows=[{"decision_id": i, "status": "not_in_hours"} for i in self.missing])
        return _Job(affected=2)


def test_reconcile_flips_only_rows_now_in_hours():
    client = _Client(missing=["d9"])
    assert pfa.reconcile_not_in_hours(client, "palmetto") == 2
    update, cfg = client.sql[-1]
    assert "SET status = 'applied'" in update and "status = 'not_in_hours'" in update
    still = {p.name: p for p in cfg.query_parameters}["still"]
    assert still.values == ["d9"]
