"""adp_schedule_write orchestration (Issue #337) — BQ and ADP are faked."""
from __future__ import annotations

import contextlib
import datetime
import unittest
from unittest import mock

from agents.bhaga.scripts import adp_schedule_write as w
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


class RunDrafts(unittest.TestCase):
    def _run(self, fq, create_shift=None, dry_run=False):
        with mock.patch.object(w, "_query", fq), \
             mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week") as goto, \
             mock.patch.object(wb, "create_shift", create_shift or mock.MagicMock()) as cs, \
             mock.patch.object(wb, "create_open_shift") as cos:
            rc = w.run_drafts("palmetto", "p1", headless=True, dry_run=dry_run)
        return rc, goto, cs, cos

    def test_creates_assigned_and_open_and_skips_duplicates(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", None), _row("c", "Roe, Sam")], done_keys=["c"])
        rc, goto, cs, cos = self._run(fq)
        self.assertEqual(rc, 0)
        goto.assert_called_once()
        self.assertEqual(goto.call_args.args[2], datetime.date(2026, 9, 28))
        cs.assert_called_once()
        self.assertEqual(cs.call_args.kwargs["employee"], "Doe, Alex")
        cos.assert_called_once()
        self.assertIn(("c", "skipped", "already drafted in ADP by an earlier save"), fq.updates)
        self.assertIn(("a", "drafted", None), fq.updates)
        self.assertIn(("b", "drafted", None), fq.updates)

    def test_one_failure_is_recorded_and_the_rest_continue(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam")])
        boom = mock.MagicMock(side_effect=[wb.ScheduleWriteError("not listed"), "Roe, Sam"])
        rc, *_ = self._run(fq, create_shift=boom)
        self.assertEqual(rc, 1)
        self.assertEqual(fq.updates[0][:2], ("a", "failed"))
        self.assertIn("not listed", fq.updates[0][2])
        self.assertEqual(fq.updates[1], ("b", "drafted", None))

    def test_unconfirmed_save_stops_the_run(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam"), _row("c", "Poe, Kim")])
        boom = mock.MagicMock(side_effect=["Doe, Alex", wb.UnconfirmedSave("count did not move")])
        rc, _g, cs, _c = self._run(fq, create_shift=boom)
        self.assertEqual(rc, 1)
        self.assertEqual(cs.call_count, 2)
        self.assertEqual(fq.updates[0], ("a", "drafted", None))
        self.assertEqual(fq.updates[1][:2], ("b", "failed"))
        self.assertEqual(fq.updates[2], ("c", "failed", "not attempted: stopped after an unconfirmed save"))

    def test_dry_run_writes_nothing(self):
        fq = FakeQuery([_row("a", "Doe, Alex")], done_keys=[])
        rc, _g, cs, _c = self._run(fq, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertTrue(cs.call_args.kwargs["dry_run"])
        self.assertEqual(fq.updates, [])


class Message(unittest.TestCase):
    def test_lists_open_shifts_in_order(self):
        text = w.clickup_message(
            datetime.date(2026, 9, 28),
            4,
            [_row("x", None, 780, 1260, datetime.date(2026, 10, 4)), _row("y", None, 600, 960)],
        )
        self.assertIn("week of Sep 28", text)
        self.assertIn("(4 shifts)", text)
        self.assertLess(text.index("Sat Oct 3 · 10 AM–4 PM"), text.index("Sun Oct 4 · 1 PM–9 PM"))

    def test_no_open_shifts(self):
        self.assertIn("No open shifts", w.clickup_message(datetime.date(2026, 9, 28), 1, []))


if __name__ == "__main__":
    unittest.main()
