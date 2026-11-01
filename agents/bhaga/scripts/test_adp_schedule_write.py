"""adp_schedule_write orchestration (Issue #337) — BQ and ADP are faked."""
from __future__ import annotations

import contextlib
import datetime
import json
import tempfile
import unittest
from collections import Counter
from unittest import mock

from agents.bhaga.scripts import adp_schedule_write as w
from agents.bhaga.scripts import daily_refresh
from skills.adp_run_automation import schedule_write_backend as wb

D = datetime.date(2026, 10, 3)


def _row(key, employee, start=600, end=960, date=D):
    return {"row_key": key, "employee": employee, "start_min": start, "end_min": end, "date": date}


class FakeQuery:
    def __init__(self, queued, done_keys=()):
        self.queued, self.done_keys, self.updates = queued, done_keys, []

    def __call__(self, sql, params):
        p = {n: v for n, _t, v in params}
        if sql.startswith("UPDATE"):
            self.updates.append((p.get("key"), p.get("status"), p.get("error")))
            return []
        if "status = 'queued'" in sql:
            return self.queued
        return [{"row_key": k} for k in self.done_keys]


@contextlib.contextmanager
def fake_session(**_kw):
    yield None, mock.MagicMock()


class FakeAdp:
    """ADP's Team Schedule as run_drafts sees it: a draft count, open shifts, assigned drafts.

    ``create`` lists one outcome per create call: "ok" saves; "unconfirmed" saves but
    raises UnconfirmedSave; "lost" raises UnconfirmedSave without saving; an exception
    instance is raised without saving.
    """

    def __init__(self, count=0, open_shifts=(), assigned=(), create=(), recheck_error=None):
        self.count = count
        self.open = Counter(open_shifts)
        self.assigned = set(assigned)
        self.create = list(create)
        self.recheck_error = recheck_error
        self.befores: list[int] = []

    def _save(self, row_slot, employee):
        outcome = self.create.pop(0) if self.create else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        if outcome != "lost":
            self.count += 1
            if employee:
                self.assigned.add((employee, *row_slot))
            else:
                self.open[row_slot] += 1
        if outcome in ("unconfirmed", "lost"):
            raise wb.UnconfirmedSave("count did not move")

    def create_shift(self, _f, _p, *, date_iso, employee, start_min, end_min, before, dry_run):
        self.befores.append(before)
        if not dry_run:
            self._save((date_iso, start_min, end_min), employee)
        return employee

    def create_open_shift(self, _f, _p, *, date_iso, start_min, end_min, before, dry_run):
        self.befores.append(before)
        if not dry_run:
            self._save((date_iso, start_min, end_min), None)

    def read_open_shifts(self, _p, _f, _week):
        return Counter(self.open)

    def has_draft_shift(self, _f, _p, *, date_iso, employee, start_min, end_min):
        if self.recheck_error:
            raise self.recheck_error
        return (employee, date_iso, start_min, end_min) in self.assigned

    def drafts_pending(self, _f):
        return self.count


class RunDrafts(unittest.TestCase):
    def _run(self, fq, adp=None, dry_run=False, known=(), goto=None):
        adp = adp or FakeAdp()
        self.adp = adp
        reopen = mock.MagicMock()
        if adp.recheck_error:
            reopen.side_effect = [mock.MagicMock(), adp.recheck_error]
        with mock.patch.object(w, "_query", fq), \
             mock.patch.object(w, "_known_open_slots", lambda _s, _w: set(known)), \
             mock.patch.object(wb, "drafts_pending", adp.drafts_pending), \
             mock.patch.object(wb, "read_open_shifts", adp.read_open_shifts), \
             mock.patch.object(wb, "has_draft_shift", adp.has_draft_shift), \
             mock.patch.object(wb, "create_shift", adp.create_shift), \
             mock.patch.object(wb, "create_open_shift", adp.create_open_shift), \
             mock.patch.object(wb, "_back_out"), \
             mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule", reopen), \
             mock.patch.object(wb, "goto_week", goto or mock.MagicMock()) as self.goto, \
             mock.patch.object(w, "refresh_schedule") as self.refresh:
            return w.run_drafts("palmetto", "p1", headless=True, dry_run=dry_run)

    def test_creates_assigned_and_open_and_skips_duplicates(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", None), _row("c", "Roe, Sam")], done_keys=["c"])
        rc = self._run(fq)
        self.assertEqual(rc, 0)
        self.assertEqual(self.goto.call_args.args[2], datetime.date(2026, 9, 28))
        self.assertEqual(self.adp.open, Counter({("2026-10-03", 600, 960): 1}))
        self.assertIn(("Doe, Alex", "2026-10-03", 600, 960), self.adp.assigned)
        self.assertIn(("c", "skipped", "already drafted in ADP by an earlier save"), fq.updates)
        self.assertIn(("a", "drafted", None), fq.updates)
        self.assertIn(("b", "drafted", None), fq.updates)
        self.refresh.assert_called_once()
        self.assertEqual(self.refresh.call_args.args[2], datetime.date(2026, 9, 28))

    def test_one_failure_is_recorded_and_the_rest_continue(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam")])
        rc = self._run(fq, FakeAdp(create=[wb.ScheduleWriteError("not listed")]))
        self.assertEqual(rc, 1)
        self.assertEqual(fq.updates[0][:2], ("a", "failed"))
        self.assertIn("not listed", fq.updates[0][2])
        self.assertEqual(fq.updates[1], ("b", "drafted", None))

    def test_each_shift_gets_the_count_adp_shows(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam")])
        self.assertEqual(self._run(fq, FakeAdp(count=6)), 0)
        self.assertEqual(self.adp.befores, [6, 7])

    def test_unconfirmed_open_shift_that_adp_shows_is_drafted_and_the_run_continues(self):
        # 2026-10-17 bhaga-daily-refresh-8v894: ADP saved the Oct 31 open shift, the job stopped.
        fq = FakeQuery([_row("a", None), _row("b", "Roe, Sam"), _row("c", None, start=900)])
        rc = self._run(fq, FakeAdp(count=16, create=["unconfirmed"]))
        self.assertEqual(rc, 0)
        self.assertEqual(fq.updates, [("a", "drafted", None), ("b", "drafted", None), ("c", "drafted", None)])
        self.assertEqual(self.adp.befores, [16, 17, 18])
        self.assertEqual(sum(self.adp.open.values()), 2)

    def test_unconfirmed_save_adp_does_not_show_is_failed_and_the_run_continues(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam")])
        rc = self._run(fq, FakeAdp(count=3, create=["lost"]))
        self.assertEqual(rc, 1)
        self.assertEqual(fq.updates[0], ("a", "failed", "not saved — ADP does not show this shift"))
        self.assertEqual(fq.updates[1], ("b", "drafted", None))
        self.assertEqual(self.adp.befores, [3, 3])

    def test_unreadable_recheck_stops_without_stranding_queued_rows(self):
        # 2026-10-16 bhaga-daily-refresh-n8mp2: the recheck itself raised and 25 rows stayed 'queued'.
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam"), _row("c", "Poe, Kim")])
        adp = FakeAdp(create=["unconfirmed"], recheck_error=wb.ScheduleWriteError("Publish drafts button not found"))
        rc = self._run(fq, adp)
        self.assertEqual(rc, 1)
        self.assertEqual(fq.updates[0][:2], ("a", "failed"))
        self.assertIn("UnconfirmedSave", fq.updates[0][2])
        self.assertIn("recheck failed: ScheduleWriteError", fq.updates[0][2])
        self.assertEqual(fq.updates[1:], [
            ("b", "failed", "not attempted: stopped after an unconfirmed save"),
            ("c", "failed", "not attempted: stopped after an unconfirmed save"),
        ])
        self.refresh.assert_called_once()

    def test_open_shift_already_in_adp_but_not_in_bq_is_adopted_not_duplicated(self):
        # Retry of 8v894's week: Oct 27's open shift is recorded; Oct 31's two were saved but not recorded.
        oct27, oct31 = datetime.date(2026, 10, 27), datetime.date(2026, 10, 31)
        adp = FakeAdp(count=26, open_shifts={
            ("2026-10-27", 990, 1230): 1, ("2026-10-31", 810, 1230): 1, ("2026-10-31", 900, 1230): 1,
        })
        fq = FakeQuery([
            _row("a", None, 810, 1230, date=oct31), _row("b", None, 900, 1230, date=oct31),
            _row("c", None, 960, 1230, date=oct31), _row("d", None, 990, 1230, date=oct27),
        ])
        rc = self._run(fq, adp, known={("2026-10-27", 990, 1230)})
        self.assertEqual(rc, 0)
        self.assertEqual(fq.updates, [("a", "drafted", None), ("b", "drafted", None),
                                      ("c", "drafted", None), ("d", "drafted", None)])
        # a + b adopted; c is new; d matches a recorded shift, so it is a second one ADP lacks.
        self.assertEqual(adp.befores, [26, 27])
        self.assertEqual(adp.open, Counter({("2026-10-27", 990, 1230): 2, ("2026-10-31", 810, 1230): 1,
                                            ("2026-10-31", 900, 1230): 1, ("2026-10-31", 960, 1230): 1}))

    def test_assigned_shift_already_in_adp_is_adopted(self):
        fq = FakeQuery([_row("a", "Doe, Alex")])
        adp = FakeAdp(assigned={("Doe, Alex", "2026-10-03", 600, 960)},
                      create=[wb.ScheduleWriteError("Doe, Alex is not in ADP's unscheduled list")])
        self.assertEqual(self._run(fq, adp), 0)
        self.assertEqual(fq.updates, [("a", "drafted", None)])

    def test_crash_marks_every_unsaved_row_failed_and_reraises(self):
        later = datetime.date(2026, 10, 10)
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam", date=later)])
        goto = mock.MagicMock(side_effect=[None, RuntimeError("session lost")])
        with self.assertRaises(RuntimeError):
            self._run(fq, goto=goto)
        self.assertEqual(fq.updates, [
            ("a", "drafted", None),
            ("b", "failed", "not attempted: job stopped (RuntimeError: session lost)"),
        ])
        self.refresh.assert_called_once()

    def test_dry_run_writes_nothing(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", None)], done_keys=[])
        rc = self._run(fq, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual((self.adp.count, self.adp.assigned, sum(self.adp.open.values())), (0, set(), 0))
        self.assertEqual(fq.updates, [])
        self.refresh.assert_not_called()


class FakeAssignQuery:
    def __init__(self, queued, assigned_keys=()):
        self.queued, self.assigned_keys, self.updates, self.tables = queued, assigned_keys, [], set()

    def __call__(self, sql, params, *, table="labor_schedule_pushes"):
        self.tables.add(table)
        p = {n: v for n, _t, v in params}
        if sql.startswith("UPDATE"):
            self.updates.append((p["key"], p["status"], p["error"], p["name"]))
            return []
        if "status = 'queued'" in sql:
            return self.queued
        return [{"row_key": k} for k in self.assigned_keys]


class FakeAssignAdp:
    """ADP's open shifts; ``outcomes`` per assign click: "ok", "ignored" (ADP keeps it open) or an exception."""

    def __init__(self, open_shifts, outcomes=(), reread_error=None):
        self.open = Counter(open_shifts)
        self.outcomes = list(outcomes)
        self.reread_error = reread_error
        self.reads = 0
        self.assigned: list[tuple] = []

    def read_open_shifts(self, _p, _f, _week):
        self.reads += 1
        if self.reread_error and self.reads > 1:
            raise self.reread_error
        return Counter(self.open)

    def assign_open_shift(self, _f, _p, *, date_iso, employee, start_min, end_min, dry_run):
        outcome = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        if not dry_run and outcome == "ok":
            self.open[(date_iso, start_min, end_min)] -= 1
            self.assigned.append((employee, date_iso, start_min, end_min))
        return employee


def _arow(key, employee, start=990, end=1230, date=datetime.date(2026, 10, 27)):
    return _row(key, employee, start, end, date)


class RunAssign(unittest.TestCase):
    OCT27 = ("2026-10-27", 990, 1230)

    def _run(self, fq, adp, dry_run=False, goto=None):
        self.adp = adp
        with mock.patch.object(w, "_query", fq), \
             mock.patch.object(wb, "read_open_shifts", adp.read_open_shifts), \
             mock.patch.object(wb, "assign_open_shift", adp.assign_open_shift), \
             mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week", goto or mock.MagicMock()) as self.goto, \
             mock.patch.object(w, "refresh_schedule") as self.refresh:
            return w.run_assign("palmetto", "a1", headless=True, dry_run=dry_run)

    def test_assigns_queued_rows_skips_earlier_ones_and_re_reads_every_changed_week(self):
        nov3 = datetime.date(2026, 11, 3)
        fq = FakeAssignQuery(
            [_arow("a", "Denton, Cimino"), _arow("b", "Huynh, Hillary", 900, 1230, date=nov3), _arow("c", "Roe, Sam")],
            assigned_keys=["c"],
        )
        adp = FakeAssignAdp({self.OCT27: 1, ("2026-11-03", 900, 1230): 1})
        self.assertEqual(self._run(fq, adp), 0)
        self.assertEqual(fq.tables, {"labor_open_shift_assignments"})
        self.assertEqual(fq.updates, [
            ("a", "assigned", None, "Denton, Cimino"),
            ("c", "skipped", "already assigned by an earlier Write to ADP", None),
            ("b", "assigned", None, "Huynh, Hillary"),
        ])
        self.refresh.assert_called_once_with(mock.ANY, "palmetto", datetime.date(2026, 10, 26), weeks=2)
        self.assertEqual(self.goto.call_args.args[2], datetime.date(2026, 10, 26))

    def test_earlier_assignment_undone_in_adp_is_assigned_again(self):
        fq = FakeAssignQuery([_arow("a", "Orourke, Elizabeth")], assigned_keys=["a"])
        adp = FakeAssignAdp({self.OCT27: 1})
        self.assertEqual(self._run(fq, adp), 0)
        self.assertEqual(fq.updates, [("a", "assigned", None, "Orourke, Elizabeth")])

    def test_two_identical_open_shifts_take_two_people(self):
        fq = FakeAssignQuery([_arow("a", "Denton, Cimino"), _arow("b", "Huynh, Hillary")])
        adp = FakeAssignAdp({self.OCT27: 2})
        self.assertEqual(self._run(fq, adp), 0)
        self.assertEqual([u[1] for u in fq.updates], ["assigned", "assigned"])
        self.assertEqual(adp.open[self.OCT27], 0)

    def test_shift_no_longer_open_fails_without_a_click(self):
        fq = FakeAssignQuery([_arow("a", "Denton, Cimino"), _arow("b", "Huynh, Hillary", 900)])
        adp = FakeAssignAdp({("2026-10-27", 900, 1230): 1})
        self.assertEqual(self._run(fq, adp), 1)
        self.assertEqual(fq.updates[0][:3], ("a", "failed", "no longer an open shift in ADP (claimed, assigned or removed)"))
        self.assertEqual(fq.updates[1][:2], ("b", "assigned"))
        self.assertEqual(adp.assigned, [("Huynh, Hillary", "2026-10-27", 900, 1230)])

    def test_adp_rejecting_a_person_is_recorded_and_the_rest_continue(self):
        fq = FakeAssignQuery([_arow("a", "Berding, Kenya"), _arow("b", "Huynh, Hillary", 900)])
        adp = FakeAssignAdp(
            {self.OCT27: 1, ("2026-10-27", 900, 1230): 1},
            outcomes=[wb.ScheduleWriteError("Berding, Kenya is not eligible for this open shift in ADP")],
        )
        self.assertEqual(self._run(fq, adp), 1)
        self.assertEqual(fq.updates[0][:2], ("a", "failed"))
        self.assertIn("not eligible", fq.updates[0][2])
        self.assertEqual(fq.updates[1][:2], ("b", "assigned"))

    def test_assign_adp_does_not_reflect_is_failed(self):
        fq = FakeAssignQuery([_arow("a", "Denton, Cimino")])
        self.assertEqual(self._run(fq, FakeAssignAdp({self.OCT27: 1}, outcomes=["ignored"])), 1)
        self.assertEqual(fq.updates, [("a", "failed", "ADP did not confirm the assignment — the open shift is still listed", None)])
        self.refresh.assert_called_once()

    def test_unreadable_re_read_is_flagged_unconfirmed(self):
        fq = FakeAssignQuery([_arow("a", "Denton, Cimino")])
        adp = FakeAssignAdp({self.OCT27: 1}, reread_error=wb.ScheduleWriteError("pane did not open"))
        self.assertEqual(self._run(fq, adp), 1)
        self.assertTrue(fq.updates[0][2].startswith("UnconfirmedAssign"))

    def test_dry_run_writes_nothing(self):
        fq = FakeAssignQuery([_arow("a", "Denton, Cimino")], assigned_keys=["x"])
        adp = FakeAssignAdp({self.OCT27: 1})
        self.assertEqual(self._run(fq, adp, dry_run=True), 0)
        self.assertEqual((fq.updates, adp.assigned), ([], []))
        self.refresh.assert_not_called()

    def test_crash_marks_unsettled_rows_failed_and_reraises(self):
        fq = FakeAssignQuery([_arow("a", "Denton, Cimino")])
        with self.assertRaises(RuntimeError):
            self._run(fq, FakeAssignAdp({self.OCT27: 1}), goto=mock.MagicMock(side_effect=RuntimeError("session lost")))
        self.assertEqual(fq.updates, [("a", "failed", "not attempted: job stopped (RuntimeError: session lost)", None)])


N3 = datetime.date(2026, 11, 3)


def _change(cid, kind, *, employee=None, start=540, end=900, date=N3, **extra):
    return {"change_id": cid, "kind": kind, "employee": employee, "start_min": start, "end_min": end,
            "date": date, "to_start_min": None, "to_end_min": None, "to_employee": None, "count": None,
            "summary": f"{kind} {cid}", "requested_by": "adi@example.com", "row_key": None, **extra}


class FakeApplyQuery:
    def __init__(self, shifts=(), unavail=()):
        self.rows = {w.SHIFT_TABLE: list(shifts), w.UNAVAIL_TABLE: list(unavail)}
        self.updates, self.events = [], []

    def __call__(self, sql, params, *, table="labor_schedule_pushes"):
        p = {n: v for n, _t, v in params}
        if sql.startswith("UPDATE"):
            self.updates.append((table, p["cid"], p["status"], p["error"]))
            return []
        if sql.startswith("INSERT"):
            self.events.append((table, p["cid"]))
            return []
        return self.rows[table]


class FakeApplyAdp:
    """Published assigned shifts, drafts + open-shift counts. ``fail`` maps a backend method to the exception it raises once."""

    def __init__(self, assigned=(), open_shifts=None, fail=None, lose_open_adds=0, drafts=()):
        self.assigned = set(assigned)
        self.drafts = set(drafts)
        self.open = Counter(open_shifts or {})
        self.fail = dict(fail or {})
        self.lose_open_adds = lose_open_adds
        self.calls: list[tuple] = []

    def _step(self, method, *what):
        self.calls.append((method, *what))
        exc = self.fail.pop(method, None)
        if exc:
            raise exc

    def shift_in_grid(self, _f, _p, *, date_iso, employee, start_min, end_min, state="any"):
        key = (employee, date_iso, start_min, end_min)
        return (state != "draft" and key in self.assigned) or (state != "published" and key in self.drafts)

    def create_shift(self, _f, _p, *, date_iso, employee, start_min, end_min, publish=False, dry_run=False):
        self._step("create_shift", employee, date_iso)
        if not dry_run:
            self.assigned.add((employee, date_iso, start_min, end_min))
        return employee

    def delete_any_shift(self, _f, _p, *, date_iso, employee, start_min, end_min, dry_run=False):
        self._step("delete_any_shift", employee, date_iso)
        if not dry_run:
            self.assigned.discard((employee, date_iso, start_min, end_min))
            self.drafts.discard((employee, date_iso, start_min, end_min))

    def edit_shift(self, _f, _p, *, date_iso, employee, start_min, end_min, to_start_min, to_end_min, dry_run=False):
        self._step("edit_shift", employee, date_iso)
        if not dry_run:
            self.assigned.discard((employee, date_iso, start_min, end_min))
            self.assigned.add((employee, date_iso, to_start_min, to_end_min))

    def read_open_shifts(self, _p, _f, _week):
        return Counter(self.open)

    def create_open_shift(self, _f, _p, *, date_iso, start_min, end_min, publish=False, dry_run=False):
        self._step("create_open_shift", date_iso)
        if dry_run:
            return
        if self.lose_open_adds:
            self.lose_open_adds -= 1
            return "Create open shift | Publish"
        self.open[(date_iso, start_min, end_min)] += 1
        return "An open shift has been published"

    def assign_open_shift(self, _f, _p, *, date_iso, employee, start_min, end_min, dry_run=False):
        self._step("assign_open_shift", employee, date_iso)
        if not dry_run:
            self.open[(date_iso, start_min, end_min)] -= 1
        return employee

    def edit_open_shift(self, _f, _p, *, date_iso, start_min, end_min, to_start_min, to_end_min, dry_run=False):
        self._step("edit_open_shift", date_iso)
        if not dry_run:
            self.open[(date_iso, start_min, end_min)] -= 1
            self.open[(date_iso, to_start_min, to_end_min)] += 1

    def delete_open_shift(self, _f, _p, *, date_iso, start_min, end_min, dry_run=False):
        self._step("delete_open_shift", date_iso)
        if not dry_run:
            self.open[(date_iso, start_min, end_min)] -= 1


class RunApply(unittest.TestCase):
    SLOT = ("2026-11-03", 540, 900)

    def _run(self, fq, adp, dry_run=False, goto=None, approve=None):
        self.adp = adp
        patches = [mock.patch.object(wb, m, getattr(adp, m)) for m in (
            "shift_in_grid", "create_shift", "delete_any_shift", "edit_shift", "read_open_shifts",
            "create_open_shift", "assign_open_shift", "edit_open_shift", "delete_open_shift")]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(w, "_query", fq))
            stack.enter_context(mock.patch("skills.adp_run_automation.runner.adp_session", fake_session))
            stack.enter_context(mock.patch("skills.adp_run_automation.runner._open_team_schedule"))
            self.goto = stack.enter_context(mock.patch.object(wb, "goto_week", goto or mock.MagicMock()))
            self.approve = stack.enter_context(mock.patch.object(
                w, "_approve_unavailability", approve or mock.MagicMock(return_value=True)))
            self.refresh = stack.enter_context(mock.patch.object(w, "refresh_schedule"))
            stack.enter_context(mock.patch.object(w, "_today_ct", return_value=datetime.date(2026, 10, 28)))
            return w.run_apply("palmetto", "b1", headless=True, dry_run=dry_run)

    def test_plan_frees_slots_before_adding_and_assigns_last(self):
        rows = [_change("a", "open-assign", employee="Roe, Sam"), _change("b", "shift-add", employee="Doe, Alex"),
                _change("c", "shift-delete", employee="Lee, Kim"), _change("d", "open-edit"),
                _change("e", "shift-reassign", employee="Lee, Kim"),
                _change("f", "shift-delete", employee="Lee, Kim", date=datetime.date(2026, 10, 30))]
        plan = w.apply_plan(rows)
        self.assertEqual([wk for wk, _ in plan], [datetime.date(2026, 10, 26), datetime.date(2026, 11, 2)])
        self.assertEqual([r["change_id"] for r in plan[1][1]], ["c", "d", "e", "b", "a"])

    def test_every_kind_is_applied_confirmed_and_logged(self):
        fq = FakeApplyQuery([
            _change("add", "shift-add", employee="Doe, Alex"),
            _change("del", "shift-delete", employee="Lee, Kim", start=600),
            _change("edit", "shift-edit", employee="Roe, Sam", to_start_min=600, to_end_min=960),
            _change("re", "shift-reassign", employee="Huynh, Hillary", start=420, to_employee="Denton, Cimino"),
            _change("oadd", "open-add", count=2, start=780, end=1080),
            _change("oedit", "open-edit", to_start_min=600, to_end_min=900),
            _change("odel", "open-delete", start=300, end=600),
            _change("oasg", "open-assign", employee="Berding, Kenya", start=420, end=660),
        ])
        adp = FakeApplyAdp(
            assigned={("Lee, Kim", "2026-11-03", 600, 900), ("Roe, Sam", "2026-11-03", 540, 900),
                      ("Huynh, Hillary", "2026-11-03", 420, 900)},
            open_shifts={self.SLOT: 1, ("2026-11-03", 300, 600): 1, ("2026-11-03", 420, 660): 1},
        )
        self.assertEqual(self._run(fq, adp), 0)
        self.assertEqual({u[1]: u[2] for u in fq.updates}, dict.fromkeys(
            ["add", "del", "edit", "re", "oadd", "oedit", "odel", "oasg"], "applied"))
        self.assertEqual(len(fq.events), 8)
        self.assertEqual({e[0] for e in fq.events}, {"adp_shift_events"})
        self.assertEqual({c[0] for c in adp.calls[:2]}, {"delete_any_shift", "delete_open_shift"})
        self.assertEqual(adp.calls[2][0], "edit_shift")
        self.assertEqual(adp.calls[-1][0], "assign_open_shift")
        self.assertIn(("Denton, Cimino", "2026-11-03", 420, 900), adp.assigned)
        self.assertEqual(adp.open[("2026-11-03", 780, 1080)], 2)
        self.refresh.assert_called_once_with(mock.ANY, "palmetto", datetime.date(2026, 11, 2), weeks=1)

    def test_changes_adp_already_shows_are_skipped_without_a_click(self):
        fq = FakeApplyQuery([
            _change("add", "shift-add", employee="Doe, Alex"),
            _change("del", "shift-delete", employee="Lee, Kim"),
            _change("odel", "open-delete"),
            _change("edit", "shift-edit", employee="Roe, Sam", to_start_min=600, to_end_min=960),
        ])
        adp = FakeApplyAdp(assigned={("Doe, Alex", "2026-11-03", 540, 900), ("Roe, Sam", "2026-11-03", 600, 960)})
        self.assertEqual(self._run(fq, adp), 0)
        self.assertEqual([u[2] for u in fq.updates], ["skipped"] * 4)
        self.assertEqual((adp.calls, fq.events), ([], []))
        self.refresh.assert_not_called()

    def test_a_shift_gone_from_adp_fails_and_the_rest_continue(self):
        fq = FakeApplyQuery([
            _change("edit", "shift-edit", employee="Roe, Sam", to_start_min=600, to_end_min=960),
            _change("oasg", "open-assign", employee="Berding, Kenya"),
            _change("add", "shift-add", employee="Doe, Alex"),
        ])
        with mock.patch("builtins.print") as out:
            self.assertEqual(self._run(fq, FakeApplyAdp()), 1)
        self.assertEqual([(u[1], u[2]) for u in fq.updates],
                         [("edit", "failed"), ("add", "applied"), ("oasg", "failed")])
        self.assertIn("no longer in ADP", fq.updates[0][3])
        self.assertIn("no longer an open shift", fq.updates[2][3])
        printed = " ".join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn("BREADCRUMB adp_apply batch_id=b1 change_id=edit kind=shift-edit", printed)

    def test_publish_and_add_make_an_adp_draft_live_and_skip_a_live_shift(self):
        fq = FakeApplyQuery([
            _change("pub", "shift-publish", employee="Doe, Alex"),
            _change("add", "shift-add", employee="Lee, Kim"),
            _change("live", "shift-publish", employee="Roe, Sam"),
            _change("gone", "shift-publish", employee="Berding, Kenya"),
        ])
        adp = FakeApplyAdp(drafts={("Doe, Alex", "2026-11-03", 540, 900), ("Lee, Kim", "2026-11-03", 540, 900)},
                           assigned={("Roe, Sam", "2026-11-03", 540, 900)})
        self.assertEqual(self._run(fq, adp), 1)
        self.assertEqual({u[1]: u[2] for u in fq.updates},
                         {"pub": "applied", "add": "applied", "live": "skipped", "gone": "failed"})
        self.assertIn("draft is no longer in ADP", next(u[3] for u in fq.updates if u[1] == "gone"))
        self.assertEqual(adp.drafts, set())
        self.assertLessEqual({("Doe, Alex", "2026-11-03", 540, 900), ("Lee, Kim", "2026-11-03", 540, 900)}, adp.assigned)
        self.assertEqual([c[0] for c in adp.calls], ["delete_any_shift", "create_shift"] * 2)

    def test_publish_whose_add_fails_says_the_draft_was_removed(self):
        fq = FakeApplyQuery([_change("pub", "shift-publish", employee="Doe, Alex")])
        adp = FakeApplyAdp(drafts={("Doe, Alex", "2026-11-03", 540, 900)},
                           fail={"create_shift": wb.ScheduleWriteError("not in ADP's unscheduled list")})
        self.assertEqual(self._run(fq, adp), 1)
        self.assertIn("removed Doe, Alex's draft but could not add it published", fq.updates[0][3])

    def test_reassign_whose_add_fails_says_the_shift_was_removed(self):
        fq = FakeApplyQuery([_change("re", "shift-reassign", employee="Huynh, Hillary", to_employee="Denton, Cimino")])
        adp = FakeApplyAdp(assigned={("Huynh, Hillary", "2026-11-03", 540, 900)},
                           fail={"create_shift": wb.ScheduleWriteError("Denton, Cimino is not in ADP's unscheduled list")})
        self.assertEqual(self._run(fq, adp), 1)
        self.assertEqual(fq.updates[0][2], "failed")
        self.assertIn("removed Huynh, Hillary's shift but could not add it for Denton, Cimino", fq.updates[0][3])
        self.refresh.assert_called_once()

    def test_open_add_adp_only_partly_shows_is_failed_with_the_count(self):
        fq = FakeApplyQuery([_change("oadd", "open-add", count=2)])
        self.assertEqual(self._run(fq, FakeApplyAdp(lose_open_adds=1)), 1)
        self.assertEqual(fq.updates[0][2:], (
            "failed",
            "UnconfirmedSave: ADP shows 1 of 2 new open shifts (ADP lists 2026-11-03: 9:00 AM-3:00 PM;"
            " after Publish ADP said: 'An open shift has been published')",
        ))
        self.assertEqual(fq.events, [])

    def test_unavailability_approve_is_applied_and_other_kinds_fail_without_a_click(self):
        fq = FakeApplyQuery(unavail=[
            {**_change("ok", "approve", employee="Doe, Alex"), "row_key": "k1"},
            {**_change("gone", "approve", employee="Lee, Kim"), "row_key": "k2"},
            {**_change("deny", "deny", employee="Roe, Sam"), "row_key": "k3"},
        ])
        approve = mock.MagicMock(side_effect=[True, False])
        self.assertEqual(self._run(fq, FakeApplyAdp(), approve=approve), 1)
        self.assertEqual([(u[0], u[1], u[2]) for u in fq.updates], [
            (w.UNAVAIL_TABLE, "ok", "applied"), (w.UNAVAIL_TABLE, "gone", "skipped"), (w.UNAVAIL_TABLE, "deny", "failed"),
        ])
        self.assertEqual(fq.events, [("adp_unavailability_events", "ok")])
        self.assertEqual(approve.call_count, 2)
        self.refresh.assert_called_once_with(mock.ANY, "palmetto", datetime.date(2026, 10, 26), weeks=1)

    def test_dry_run_walks_each_change_and_writes_nothing(self):
        fq = FakeApplyQuery([
            _change("add", "shift-add", employee="Doe, Alex"),
            _change("re", "shift-reassign", employee="Huynh, Hillary", to_employee="Denton, Cimino"),
            _change("oadd", "open-add", count=3),
        ])
        adp = FakeApplyAdp(assigned={("Huynh, Hillary", "2026-11-03", 540, 900)})
        self.assertEqual(self._run(fq, adp, dry_run=True), 0)
        self.assertEqual((fq.updates, fq.events), ([], []))
        self.assertEqual([c[0] for c in adp.calls], ["delete_any_shift", "create_shift", "create_open_shift"])
        self.assertIn(("Huynh, Hillary", "2026-11-03", 540, 900), adp.assigned)
        self.refresh.assert_not_called()

    def test_crash_marks_every_unsettled_change_failed_and_reraises(self):
        fq = FakeApplyQuery([_change("add", "shift-add", employee="Doe, Alex")],
                            unavail=[{**_change("ok", "approve", employee="Lee, Kim"), "row_key": "k1"}])
        with self.assertRaises(RuntimeError):
            self._run(fq, FakeApplyAdp(), goto=mock.MagicMock(side_effect=RuntimeError("session lost")))
        self.assertEqual([(u[1], u[2], u[3]) for u in fq.updates], [
            ("add", "failed", "not attempted: job stopped (RuntimeError: session lost)"),
            ("ok", "failed", "not attempted: job stopped (RuntimeError: session lost)"),
        ])

    def test_cli_and_cloud_run_dispatch_reach_run_apply(self):
        with mock.patch.object(w, "run_apply", return_value=0) as ra:
            self.assertEqual(w.main(["--store", "palmetto", "--apply-batch", "b9", "--headless"]), 0)
        ra.assert_called_once_with("palmetto", "b9", headless=True, dry_run=False)


class QueueChanges(unittest.TestCase):
    TODAY = datetime.date(2026, 10, 28)

    def test_parses_each_kind_like_the_console(self):
        rows = w.parse_changes(
            "open-add 2026-11-03 10:00-14:00 count=2; open-edit 2026-11-03 10:00-14:00 to=11:00-15:00;"
            "shift-reassign 2026-11-04 9:00-15:00 employee=Doe,_Alex to_employee=Lee,_Kim", self.TODAY)
        self.assertEqual([(r["kind"], r["date"].isoformat(), r["start_min"], r["end_min"]) for r in rows], [
            ("open-add", "2026-11-03", 600, 840), ("open-edit", "2026-11-03", 600, 840),
            ("shift-reassign", "2026-11-04", 540, 900)])
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual((rows[1]["to_start_min"], rows[1]["to_end_min"]), (660, 900))
        self.assertEqual((rows[2]["employee"], rows[2]["to_employee"]), ("Doe, Alex", "Lee, Kim"))

    def test_refuses_what_the_console_refuses(self):
        for spec in (
            "open-add 2026-10-28 10:00-14:00",                 # today
            "open-add 2026-11-03 14:00-10:00",                 # end before start
            "open-add 2026-11-03 10:00-14:00 count=11",
            "open-edit 2026-11-03 10:00-14:00 to=10:00-14:00",  # no change
            "shift-delete 2026-11-03 10:00-14:00",              # no person
            "shift-reassign 2026-11-03 10:00-14:00 employee=A to_employee=A",
            "open-add 2026-11-03 10:00-14:00 colour=red",
            "approve 2026-11-03 10:00-14:00",
            "open-add 2026-11-03",
            "",
        ):
            with self.assertRaises(ValueError, msg=spec):
                w.parse_changes(spec, self.TODAY)

    def test_queue_then_apply_one_batch(self):
        calls = []
        fq_insert = mock.MagicMock(side_effect=lambda sql, params, *, table: calls.append((table, dict(
            (n, v) for n, _t, v in params))) or [])
        with mock.patch.object(w, "_query", fq_insert), \
                mock.patch.object(w, "_today_ct", return_value=self.TODAY), \
                mock.patch.object(w, "run_apply", return_value=0) as ra:
            self.assertEqual(w.main(["--queue-changes", "open-delete 2026-11-03 10:00-14:00", "--headless"]), 0)
        (table, p), = calls
        self.assertEqual((table, p["by"]), (w.SHIFT_TABLE, "agent:cli"))
        (item,) = json.loads(p["items"])
        self.assertEqual((item["kind"], item["date"], item["start_min"], item["count"]), ("open-delete", "2026-11-03", 600, None))
        ra.assert_called_once_with("palmetto", p["batch"], headless=True, dry_run=False)

    def test_dry_run_walks_unqueued_rows_and_writes_nothing(self):
        adp = FakeApplyAdp(open_shifts={("2026-11-03", 600, 840): 1})
        query = mock.MagicMock(side_effect=AssertionError("dry run must not touch BQ"))
        with mock.patch.object(w, "_query", query), \
                mock.patch.object(w, "_today_ct", return_value=self.TODAY), \
                mock.patch.object(w, "run_apply", wraps=w.run_apply) as ra:
            patches = [mock.patch.object(wb, m, getattr(adp, m)) for m in (
                "shift_in_grid", "read_open_shifts", "create_open_shift", "delete_open_shift")]
            with contextlib.ExitStack() as stack:
                for patch in patches:
                    stack.enter_context(patch)
                stack.enter_context(mock.patch("skills.adp_run_automation.runner.adp_session", fake_session))
                stack.enter_context(mock.patch("skills.adp_run_automation.runner._open_team_schedule"))
                stack.enter_context(mock.patch.object(wb, "goto_week"))
                code = w.main(["--queue-changes",
                               "open-add 2026-11-03 10:00-14:00 count=2; open-delete 2026-11-03 10:00-14:00",
                               "--dry-run", "--headless"])
        self.assertEqual(code, 0)
        self.assertTrue(ra.call_args.kwargs["dry_run"])
        self.assertEqual([c[0] for c in adp.calls], ["delete_open_shift", "create_open_shift"])
        self.assertEqual(adp.open[("2026-11-03", 600, 840)], 1)

    def test_cloud_run_apply_with_changes_env_queues_them(self):
        env = {"BHAGA_ADP_SCHEDULE_WRITE": "apply", "BHAGA_SCHEDULE_CHANGES": "open-add 2026-11-03 10:00-14:00"}
        with mock.patch.dict("os.environ", env, clear=True), mock.patch.object(w, "main", return_value=0) as m, \
                mock.patch("sys.argv", ["daily_refresh", "--store", "palmetto"]):
            self.assertEqual(daily_refresh.main(), 0)
        argv = m.call_args.args[0]
        self.assertEqual(argv[argv.index("--queue-changes") + 1], "open-add 2026-11-03 10:00-14:00")
        self.assertNotIn("--apply-batch", argv)


class RunDelete(unittest.TestCase):
    W = datetime.date(2026, 9, 28)

    def _run(self, latest, keys, delete_shift=None, dry_run=False, counts=(10,)):
        updates = []

        def fq(sql, params):
            p = {n: v for n, _t, v in params}
            if sql.startswith("UPDATE"):
                updates.append((p["key"], p["push"], p["status"]))
                return []
            return latest

        with mock.patch.object(w, "_query", fq), \
             mock.patch.object(wb, "drafts_pending", mock.MagicMock(side_effect=list(counts))), \
             mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week"), \
             mock.patch.object(wb, "delete_shift", delete_shift or mock.MagicMock()) as ds, \
             mock.patch.object(w, "refresh_schedule") as self.refresh:
            rc = w.run_delete("palmetto", self.W, keys, headless=True, dry_run=dry_run)
        return rc, ds, updates

    @staticmethod
    def _latest(key, status="drafted", employee="Doe, Alex"):
        return {**_row(key, employee), "status": status, "push_id": "p2"}

    def test_deletes_with_a_falling_baseline_and_marks_deleted(self):
        rc, ds, updates = self._run([self._latest("a"), self._latest("b")], ["a", "b"])
        self.assertEqual(rc, 0)
        self.assertEqual([c.kwargs["before"] for c in ds.call_args_list], [10, 9])
        self.assertEqual(updates, [("a", "p2", "deleted"), ("b", "p2", "deleted")])
        self.refresh.assert_called_once_with(mock.ANY, "palmetto", self.W)

    def test_refuses_rows_that_are_not_assigned_drafts(self):
        for latest in ([self._latest("a", status="published")], [self._latest("a", employee=None)], []):
            rc, ds, updates = self._run(latest, ["a"])
            self.assertEqual(rc, 2)
            ds.assert_not_called()
            self.assertEqual(updates, [])

    def test_failure_stops_the_run(self):
        boom = mock.MagicMock(side_effect=wb.UnconfirmedSave("count"))
        rc, ds, updates = self._run([self._latest("a"), self._latest("b")], ["a", "b"], delete_shift=boom)
        self.assertEqual(rc, 1)
        self.assertEqual(ds.call_count, 1)
        self.assertEqual(updates, [])
        self.refresh.assert_not_called()

    def test_lagging_count_confirmed_by_recheck_continues(self):
        boom = mock.MagicMock(side_effect=[wb.UnconfirmedSave("slow"), None])
        rc, ds, updates = self._run([self._latest("a"), self._latest("b")], ["a", "b"],
                                    delete_shift=boom, counts=(10, 9))
        self.assertEqual(rc, 0)
        self.assertEqual(ds.call_args.kwargs["before"], 9)
        self.assertEqual(updates, [("a", "p2", "deleted"), ("b", "p2", "deleted")])

    def test_recheck_without_the_drop_stops(self):
        boom = mock.MagicMock(side_effect=wb.UnconfirmedSave("slow"))
        rc, ds, updates = self._run([self._latest("a")], ["a"], delete_shift=boom, counts=(10, 10))
        self.assertEqual(rc, 1)
        self.assertEqual(updates, [])

    def test_dry_run_writes_nothing(self):
        rc, ds, updates = self._run([self._latest("a")], ["a"], dry_run=True)
        self.assertEqual(rc, 0)
        self.assertTrue(ds.call_args.kwargs["dry_run"])
        self.assertEqual(updates, [])


if __name__ == "__main__":
    unittest.main()


class PublishedRowKeys(unittest.TestCase):
    rows = [
        {"row_key": "a", "date": "2026-10-12", "employee": "Johnson, Dolce", "start_min": 390, "end_min": 870},
        {"row_key": "b", "date": "2026-10-13", "employee": None, "start_min": 900, "end_min": 1230},
        {"row_key": "c", "date": "2026-10-17", "employee": "Huynh, Hillary", "start_min": 870, "end_min": 1200},
        {"row_key": "d", "date": "2026-10-18", "employee": "Huang, Wing", "start_min": 540, "end_min": 870},
    ]

    def test_published_in_adp_matches_by_person_prefix_times_and_open(self):
        states = [
            {"date": "2026-10-12", "employee": "Johnson, Dolce J", "start_min": 390, "end_min": 870, "draft": False},
            {"date": "2026-10-13", "employee": None, "start_min": 900, "end_min": 1230, "draft": False},
            # ADP unassigned Hillary's shift (approved unavailability) and it was published open.
            {"date": "2026-10-17", "employee": None, "start_min": 870, "end_min": 1200, "draft": False},
            {"date": "2026-10-18", "employee": "Huang, Wing", "start_min": 540, "end_min": 870, "draft": True},
        ]
        self.assertEqual(w.published_row_keys(self.rows, states), ["a", "b", "c"])

    def test_moved_or_reassigned_shift_is_not_published(self):
        states = [
            {"date": "2026-10-12", "employee": "Johnson, Dolce", "start_min": 420, "end_min": 870, "draft": False},
            {"date": "2026-10-13", "employee": "Perales, Elizabeth", "start_min": 900, "end_min": 1230, "draft": False},
            {"date": "2026-10-18", "employee": "Huang, Winger", "start_min": 540, "end_min": 870, "draft": False},
        ]
        self.assertEqual(w.published_row_keys(self.rows, states), [])


class FoundFailedRows(unittest.TestCase):
    def test_failed_rows_adp_shows_take_its_status(self):
        # 2026-10-17: Oct 31's three open shifts were saved in ADP, but their rows stayed 'failed'.
        rows = [
            {"row_key": "o1", "date": "2026-10-31", "employee": None, "start_min": 810, "end_min": 1230},
            {"row_key": "o2", "date": "2026-10-31", "employee": None, "start_min": 900, "end_min": 1230},
            {"row_key": "a", "date": "2026-11-01", "employee": "Doe, Alex", "start_min": 390, "end_min": 840},
            {"row_key": "gone", "date": "2026-11-01", "employee": None, "start_min": 540, "end_min": 990},
        ]
        states = [
            {"date": "2026-10-31", "employee": None, "start_min": 810, "end_min": 1230, "draft": True},
            {"date": "2026-10-31", "employee": None, "start_min": 900, "end_min": 1230, "draft": False},
            {"date": "2026-11-01", "employee": "Doe, Alex Q", "start_min": 390, "end_min": 840, "draft": True},
        ]
        self.assertEqual(w.found_failed_rows(rows, states), {"o1": "drafted", "o2": "published", "a": "drafted"})

    def test_near_misses_stay_failed(self):
        rows = [
            {"row_key": "a", "date": "2026-11-01", "employee": "Doe, Alex", "start_min": 390, "end_min": 840},
            {"row_key": "o", "date": "2026-11-01", "employee": None, "start_min": 540, "end_min": 990},
        ]
        states = [
            {"date": "2026-10-31", "employee": "Doe, Alex", "start_min": 390, "end_min": 840, "draft": True},
            {"date": "2026-11-01", "employee": None, "start_min": 390, "end_min": 840, "draft": True},
            {"date": "2026-11-01", "employee": "Roe, Sam", "start_min": 540, "end_min": 990, "draft": True},
        ]
        self.assertEqual(w.found_failed_rows(rows, states), {})


class ReconcileWithAdp(unittest.TestCase):
    def test_failed_row_found_in_adp_updates_only_its_latest_push(self):
        latest = [
            {"push_id": "p2", "row_key": "o1", "status": "failed", "date": "2026-10-31",
             "employee": None, "start_min": 810, "end_min": 1230},
            {"push_id": "p2", "row_key": "x", "status": "failed", "date": "2026-11-01",
             "employee": None, "start_min": 540, "end_min": 990},
        ]
        calls = []

        def fq(sql, params):
            calls.append((sql, {n: v for n, _t, v in params}))
            return latest if sql.startswith("SELECT") else []

        states = [{"date": "2026-10-31", "employee": None, "start_min": 810, "end_min": 1230, "draft": True}]
        with mock.patch.object(w, "_query", fq):
            w.reconcile_published("palmetto", states)
        updates = [p for s, p in calls if s.startswith("UPDATE")]
        self.assertEqual(updates, [{"store": "palmetto", "status": "drafted", "ids": "p2|o1"}])


D = datetime.date


def _slot(d, start, end):
    return {"date": d, "start_min": start, "end_min": end}


class PublishMessage(unittest.TestCase):
    # The operator's 2026-10-09 Shift Coverage post, with ADP's open shifts that morning.
    OCT_9_SHIFTS = [
        _slot(D(2026, 10, 18), 810, 1230),
        _slot(D(2026, 10, 17), 870, 1200),
        _slot(D(2026, 10, 13), 900, 1230),
        _slot(D(2026, 10, 17), 750, 1230),
        _slot(D(2026, 10, 16), 900, 1230),
    ]

    def test_matches_the_operator_post(self):
        msg = w.publish_message(D(2026, 10, 19), self.OCT_9_SHIFTS)
        post = msg.split("---\n\n", 1)[1]
        self.assertEqual(post, "\n".join([
            "@everyone 🗞️ Hi team! The schedule for **Oct 19–25** is now published in ADP. Please take a "
            "moment to look over your shifts and make sure they work with your availability. If anything "
            "doesn't look right, just let me know in this thread and we'll sort it out together.",
            "",
            "Additionally, sharing the Open Shifts for the coming days where we really need folks, please "
            "let us know if someone can pick these up. We are flexible around below times. 🙏🏽",
            "",
            "1. Oct 13 (Tuesday)",
            "    1. 3:00 PM → 8:30 PM",
            "2. Oct 16 (Friday)",
            "    1. 3:00 PM → 8:30 PM",
            "3. Oct 17 (Saturday)",
            "    1. 12:30 PM → 8:30 PM",
            "    2. 2:30 PM → 8:00 PM",
            "4. Oct 18 (Sunday)",
            "    1. 1:30 PM → 8:30 PM",
        ]))
        self.assertTrue(msg.startswith("Published **Oct 19–25** in ADP."))
        self.assertNotIn("re-read failed", msg)

    def test_no_open_shifts_and_month_boundary(self):
        msg = w.publish_message(D(2026, 10, 26), [])
        self.assertIn("Oct 26–Nov 1", msg)
        self.assertNotIn("Open Shifts", msg)
        self.assertTrue(msg.endswith("sort it out together."))

    def test_stale_data_is_flagged_in_the_header_only(self):
        msg = w.publish_message(D(2026, 10, 19), self.OCT_9_SHIFTS, stale_as_of="Oct 9 12:58 PM CT")
        head, post = msg.split("---\n\n", 1)
        self.assertIn("as of Oct 9 12:58 PM CT", head)
        self.assertNotIn("re-read failed", post)

    def test_window_starts_tomorrow_and_ends_on_the_published_sunday(self):
        self.assertEqual(w.open_shift_window(D(2026, 10, 19), D(2026, 10, 9)), (D(2026, 10, 10), D(2026, 10, 25)))


BROWSER_SEND = "agents.bhaga.scripts.unavailability_reminder.browser_send"


class NotifyPublished(unittest.TestCase):
    def setUp(self):
        # The REST path is the default under test; the browser path has its own tests below.
        p = mock.patch(BROWSER_SEND, side_effect=RuntimeError("no ClickUp session"))
        self.browser = p.start()
        self.addCleanup(p.stop)

    def _notify(self, fresh=True, scraped="2026-10-09T17:58:30+00:00"):
        calls = []

        def fq(sql, params):
            calls.append(dict((n, v) for n, _t, v in params))
            return [{"date": D(2026, 10, 13), "shift_range": "3:00 PM - 8:30 PM", "scraped_at_utc": scraped}]

        posted = []
        with mock.patch.object(w, "_query", fq), \
             mock.patch.object(w, "_today_ct", return_value=D(2026, 10, 9)), \
             mock.patch("core.datastore.fq", lambda t: t), \
             mock.patch("skills.clickup_chat.ensure_dm_channel", return_value={"id": "dm1"}), \
             mock.patch("skills.clickup_chat.post_message", lambda ch, content, team_id: posted.append(content)):
            w.notify_published("palmetto", D(2026, 10, 19), fresh=fresh)
        return calls, posted

    def test_reads_the_whole_window_and_posts_once(self):
        calls, posted = self._notify()
        self.assertEqual(calls, [{"lo": D(2026, 10, 10), "hi": D(2026, 10, 25)}])
        self.assertEqual(len(posted), 1)
        self.assertIn("1. Oct 13 (Tuesday)\n    1. 3:00 PM → 8:30 PM", posted[0])

    def test_failed_re_read_says_when_the_data_is_from(self):
        _calls, posted = self._notify(fresh=False)
        self.assertIn("as of Oct 9 12:58 PM CT", posted[0])

    def test_dm_failure_leaves_a_breadcrumb(self):
        with mock.patch.object(w, "_query", side_effect=RuntimeError("bq down")), \
             mock.patch("builtins.print") as out:
            w.notify_published("palmetto", D(2026, 10, 19))
        self.assertIn("BREADCRUMB adp_publish_dm", out.call_args[0][0])

    def test_channel_post_starts_at_everyone_in_shift_coverage(self):
        posted = []
        with mock.patch.object(w, "_query", return_value=[
                 {"date": D(2026, 10, 13), "shift_range": "3:00 PM - 8:30 PM", "scraped_at_utc": "2026-10-09T17:58:30+00:00"},
             ]), \
             mock.patch.object(w, "_today_ct", return_value=D(2026, 10, 9)), \
             mock.patch("core.datastore.fq", lambda t: t), \
             mock.patch("skills.clickup_chat.post_message", lambda ch, content, team_id: posted.append((ch, team_id, content))):
            self.assertTrue(w.notify_published("palmetto", D(2026, 10, 19), to="channel"))
        team_id, channel_id = w.shift_coverage_target("palmetto")
        ch, team, content = posted[0]
        self.assertEqual((ch, team), (channel_id, team_id))
        self.assertTrue(content.startswith("@everyone"))
        self.assertNotIn("copy and post", content)
        self.assertNotIn("---", content)

    def test_stale_read_is_not_posted_to_the_team(self):
        with mock.patch("skills.clickup_chat.post_message") as post, \
             mock.patch("builtins.print") as out:
            self.assertFalse(w.notify_published("palmetto", D(2026, 10, 19), fresh=False, to="channel"))
        post.assert_not_called()
        self.assertIn("not posted to the team", out.call_args[0][0])

    def _shift_rows(self):
        return [{"date": D(2026, 10, 13), "shift_range": "3:00 PM - 8:30 PM", "scraped_at_utc": "2026-10-09T17:58:30+00:00"}]

    def test_channel_post_is_typed_in_the_web_app(self):
        self.browser.side_effect = None
        with mock.patch.object(w, "_query", return_value=self._shift_rows()), \
             mock.patch.object(w, "_today_ct", return_value=D(2026, 10, 9)), \
             mock.patch("core.datastore.fq", lambda t: t), \
             mock.patch("skills.clickup_chat.post_message") as post:
            self.assertTrue(w.notify_published("palmetto", D(2026, 10, 19), to="channel"))
        team_id, channel_id = w.shift_coverage_target("palmetto")
        store, ch, content, team = self.browser.call_args[0]
        self.assertEqual((store, ch, team), ("palmetto", channel_id, team_id))
        self.assertTrue(content.startswith("@everyone"))
        post.assert_not_called()

    def test_browser_failure_falls_back_to_the_api_with_a_breadcrumb(self):
        posted = []
        with mock.patch.object(w, "_query", return_value=self._shift_rows()), \
             mock.patch.object(w, "_today_ct", return_value=D(2026, 10, 9)), \
             mock.patch("core.datastore.fq", lambda t: t), \
             mock.patch("skills.clickup_chat.post_message", lambda ch, content, team_id: posted.append(ch)), \
             mock.patch("builtins.print") as out:
            self.assertTrue(w.notify_published("palmetto", D(2026, 10, 19), to="channel"))
        _team_id, channel_id = w.shift_coverage_target("palmetto")
        self.assertEqual(posted, [channel_id])
        self.assertIn("BREADCRUMB adp_publish_browser", out.call_args_list[0][0][0])

    def test_dm_draft_stays_on_the_api(self):
        with mock.patch.object(w, "_query", return_value=self._shift_rows()), \
             mock.patch.object(w, "_today_ct", return_value=D(2026, 10, 9)), \
             mock.patch("core.datastore.fq", lambda t: t), \
             mock.patch("skills.clickup_chat.ensure_dm_channel", return_value={"id": "dm1"}), \
             mock.patch("skills.clickup_chat.post_message") as post:
            self.assertTrue(w.notify_published("palmetto", D(2026, 10, 19)))
        self.browser.assert_not_called()
        self.assertEqual(post.call_args[0][0], "dm1")


class RefreshWindow(unittest.TestCase):
    def test_reads_from_this_week_through_the_published_week(self):
        with mock.patch.object(w, "_today_ct", return_value=D(2026, 10, 9)), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week") as goto, \
             mock.patch.object(w, "refresh_schedule", return_value=True) as refresh:
            self.assertTrue(w.refresh_open_shift_window(object(), "palmetto", D(2026, 10, 19)))
        self.assertEqual(goto.call_args[0][2], D(2026, 10, 5))
        refresh.assert_called_once_with(mock.ANY, "palmetto", D(2026, 10, 5), weeks=3)

    def test_sunday_publish_starts_next_week(self):
        with mock.patch.object(w, "_today_ct", return_value=D(2026, 10, 11)), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week") as goto, \
             mock.patch.object(w, "refresh_schedule", return_value=True) as refresh:
            w.refresh_open_shift_window(object(), "palmetto", D(2026, 10, 19))
        refresh.assert_called_once_with(mock.ANY, "palmetto", D(2026, 10, 12), weeks=2)
        self.assertEqual(goto.call_count, 1)


class RunPublish(unittest.TestCase):
    W = datetime.date(2026, 10, 12)

    def _run(self, publish, refreshed=True, drafted_left=0, reloaded=None, drafted_before=33):
        sqls = []
        drafted = iter([drafted_before])

        def fq(sql, params):
            sqls.append(sql)
            if sql.startswith("SELECT status"):
                n = next(drafted, drafted_left)
                return [{"status": "drafted"}] * n + [{"status": "published"}]
            return []

        with mock.patch.object(w, "_query", fq), \
             mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week"), \
             mock.patch.object(wb, "publish_drafts", publish), \
             mock.patch.object(w, "_reloaded_draft_count", return_value=reloaded), \
             mock.patch.object(w, "refresh_schedule", return_value=refreshed), \
             mock.patch.object(w, "notify_published") as notify:
            rc = w.run_publish("palmetto", self.W, headless=True, dry_run=False)
        published = any(s.startswith("UPDATE") and "'published'" in s for s in sqls)
        return rc, notify, published

    def test_publish_marks_rows_and_sends_the_dm(self):
        rc, notify, published = self._run(mock.MagicMock(return_value=33))
        self.assertEqual((rc, published), (0, True))
        notify.assert_called_once_with("palmetto", self.W, fresh=True)

    def test_lagging_open_shift_drafts_but_reload_shows_zero_succeeds(self):
        # Oct 26 week: 26 assigned published, open-shift cells still said "Drafts: N", reload showed 0.
        publish = mock.MagicMock(side_effect=wb.PublishUnconfirmed("still shows drafts", 33))
        rc, notify, published = self._run(publish, reloaded=0, drafted_left=7)
        self.assertEqual((rc, published), (0, True))
        notify.assert_called_once()

    def test_unreadable_reload_confirmed_by_the_schedule_still_succeeds(self):
        publish = mock.MagicMock(side_effect=wb.PublishUnconfirmed("still shows drafts", 33))
        rc, notify, published = self._run(publish, reloaded=None)
        self.assertEqual((rc, published), (0, True))
        notify.assert_called_once()

    def test_drafts_still_in_adp_fail_without_a_dm(self):
        publish = mock.MagicMock(side_effect=wb.PublishUnconfirmed("still shows drafts", 33))
        for kwargs in ({"drafted_left": 2}, {"refreshed": False}, {"reloaded": 4, "drafted_left": 1}):
            rc, notify, published = self._run(publish, **kwargs)
            self.assertEqual((rc, published), (1, False))
            notify.assert_not_called()

    def test_already_published_in_adp_but_drafted_in_bq_marks_and_sends_the_dm(self):
        rc, notify, published = self._run(mock.MagicMock(return_value=0), drafted_before=7)
        self.assertEqual((rc, published), (0, True))
        notify.assert_called_once_with("palmetto", self.W, fresh=True)

    def test_nothing_to_publish_sends_no_dm(self):
        rc, notify, _published = self._run(mock.MagicMock(return_value=0), drafted_before=0)
        self.assertEqual(rc, 0)
        notify.assert_not_called()


class RunPublishDm(unittest.TestCase):
    W = datetime.date(2026, 10, 26)

    def _run(self, *, session=fake_session, refreshed=True, sent=True):
        with mock.patch("skills.adp_run_automation.runner.adp_session", session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week"), \
             mock.patch.object(w, "refresh_open_shift_window", return_value=refreshed), \
             mock.patch.object(w, "notify_published", return_value=sent) as notify:
            rc = w.run_publish_dm("palmetto", self.W, headless=True)
        return rc, notify

    def test_re_reads_adp_then_sends(self):
        rc, notify = self._run()
        self.assertEqual(rc, 0)
        notify.assert_called_once_with("palmetto", self.W, fresh=True, to="dm")

    def test_channel_choice_is_passed_through(self):
        with mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week"), \
             mock.patch.object(w, "refresh_open_shift_window", return_value=True), \
             mock.patch.object(w, "notify_published", return_value=True) as notify:
            w.run_publish_dm("palmetto", self.W, headless=True, to="channel")
        notify.assert_called_once_with("palmetto", self.W, fresh=True, to="channel")

    def test_adp_sign_in_failure_still_sends_flagged_stale(self):
        def broken(**_kw):
            raise RuntimeError("login")

        rc, notify = self._run(session=broken)
        self.assertEqual(rc, 0)
        notify.assert_called_once_with("palmetto", self.W, fresh=False, to="dm")

    def test_dm_failure_fails_the_job(self):
        rc, _notify = self._run(sent=False)
        self.assertEqual(rc, 1)


class QueueAssign(unittest.TestCase):
    TODAY = datetime.date(2026, 10, 19)
    ITEM = {"date": "2026-10-27", "employee": "Orourke, Elizabeth", "start_min": 990, "end_min": 1230}

    def _file(self) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump([self.ITEM], handle)
        handle.close()
        self.addCleanup(lambda: __import__("os").unlink(handle.name))
        return handle.name

    def test_rejects_today_and_two_shifts_for_one_person(self):
        with self.assertRaises(ValueError):
            w.validate_assign_items(
                [{"date": "2026-10-19", "employee": "A", "start_min": 1, "end_min": 2}], self.TODAY)
        with self.assertRaises(ValueError) as ctx:
            w.validate_assign_items([self.ITEM, {**self.ITEM, "start_min": 600, "end_min": 700}], self.TODAY)
        self.assertIn("already takes", str(ctx.exception))

    def test_a_run_that_does_not_write_the_schedule_does_not_block(self):
        other = {"name": "exec/2", "template": {"containers": [
            {"env": [{"name": "BHAGA_SKIP_ADP", "value": "1"}]}]}}
        self.assertIsNone(w.running_schedule_write([other]))

    def test_busy_execution_refuses_before_insert(self):
        running = {"name": "exec/1", "template": {"containers": [
            {"env": [{"name": "BHAGA_ADP_SCHEDULE_WRITE", "value": "assign"}]}]}}
        with mock.patch.object(w, "_insert_queued_assign") as insert:
            with self.assertRaises(w.ScheduleWriteBusy):
                w.queue_assign("palmetto", [self.ITEM], by="t", today=self.TODAY, executions=[running])
        insert.assert_not_called()

    def test_a_finished_write_does_not_block(self):
        done = {"name": "exec/1", "completionTime": "t", "template": {"containers": [
            {"env": [{"name": "BHAGA_ADP_SCHEDULE_WRITE", "value": "publish"}]}]}}
        with mock.patch.object(w, "_insert_queued_assign", return_value="id-1") as insert:
            self.assertEqual(
                w.queue_assign("palmetto", [self.ITEM], by="t", today=self.TODAY, executions=[done]), "id-1")
        insert.assert_called_once()

    def test_cli_refuses_while_busy_and_does_not_queue(self):
        running = {"name": "exec/9", "template": {"containers": [
            {"env": [{"name": "BHAGA_ADP_SCHEDULE_WRITE", "value": "drafts"}]}]}}
        with mock.patch.object(w, "fetch_job_executions", return_value=[running]), \
             mock.patch.object(w, "_insert_queued_assign") as insert, \
             mock.patch.object(w, "start_assign_job") as start:
            rc = w.main(["--queue-assign", self._file(), "--execute"])
        self.assertEqual(rc, 1)
        insert.assert_not_called()
        start.assert_not_called()

    def test_cli_queues_without_starting_the_job(self):
        with mock.patch.object(w, "fetch_job_executions", return_value=[]), \
             mock.patch.object(w, "_today_ct", return_value=datetime.date(2026, 10, 20)), \
             mock.patch.object(w, "_insert_queued_assign", return_value="abc") as insert, \
             mock.patch.object(w, "start_assign_job") as start:
            rc = w.main(["--queue-assign", self._file()])
        self.assertEqual(rc, 0)
        insert.assert_called_once()
        start.assert_not_called()
