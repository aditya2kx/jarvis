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
    def _run(self, fq, create_shift=None, dry_run=False, counts=(0,)):
        with mock.patch.object(w, "_query", fq), \
             mock.patch.object(wb, "drafts_pending", mock.MagicMock(side_effect=list(counts))), \
             mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
             mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
             mock.patch.object(wb, "goto_week") as goto, \
             mock.patch.object(wb, "create_shift", create_shift or mock.MagicMock()) as cs, \
             mock.patch.object(wb, "create_open_shift") as cos, \
             mock.patch.object(w, "refresh_schedule") as self.refresh:
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
        self.refresh.assert_called_once()
        self.assertEqual(self.refresh.call_args.args[2], datetime.date(2026, 9, 28))

    def test_one_failure_is_recorded_and_the_rest_continue(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam")])
        boom = mock.MagicMock(side_effect=[wb.ScheduleWriteError("not listed"), "Roe, Sam"])
        rc, *_ = self._run(fq, create_shift=boom)
        self.assertEqual(rc, 1)
        self.assertEqual(fq.updates[0][:2], ("a", "failed"))
        self.assertIn("not listed", fq.updates[0][2])
        self.assertEqual(fq.updates[1], ("b", "drafted", None))

    def test_each_shift_gets_the_tracked_baseline(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam")])
        rc, _g, cs, _c = self._run(fq, counts=(6,))
        self.assertEqual(rc, 0)
        self.assertEqual([c.kwargs["before"] for c in cs.call_args_list], [6, 7])

    def test_lagging_count_confirmed_by_recheck_continues(self):
        fq = FakeQuery([_row("a", None), _row("b", "Roe, Sam")])
        with mock.patch.object(wb, "create_open_shift", mock.MagicMock(side_effect=wb.UnconfirmedSave("slow"))):
            with mock.patch.object(w, "_query", fq), \
                 mock.patch.object(wb, "drafts_pending", mock.MagicMock(side_effect=[6, 7])), \
                 mock.patch("skills.adp_run_automation.runner.adp_session", fake_session), \
                 mock.patch("skills.adp_run_automation.runner._open_team_schedule"), \
                 mock.patch.object(wb, "goto_week"), \
                 mock.patch.object(wb, "create_shift") as cs, \
                 mock.patch.object(w, "refresh_schedule"):
                rc = w.run_drafts("palmetto", "p1", headless=True, dry_run=False)
        self.assertEqual(rc, 0)
        self.assertEqual(fq.updates, [("a", "drafted", None), ("b", "drafted", None)])
        self.assertEqual(cs.call_args.kwargs["before"], 7)

    def test_recheck_showing_no_new_draft_marks_failed_and_continues(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam")])
        boom = mock.MagicMock(side_effect=[wb.UnconfirmedSave("count did not move"), "Roe, Sam"])
        rc, _g, cs, _c = self._run(fq, create_shift=boom, counts=(3, 3))
        self.assertEqual(rc, 1)
        self.assertEqual(fq.updates[0][:2], ("a", "failed"))
        self.assertEqual(fq.updates[1], ("b", "drafted", None))
        self.assertEqual(cs.call_args.kwargs["before"], 3)

    def test_unconfirmed_save_stops_the_run(self):
        fq = FakeQuery([_row("a", "Doe, Alex"), _row("b", "Roe, Sam"), _row("c", "Poe, Kim")])
        boom = mock.MagicMock(side_effect=["Doe, Alex", wb.UnconfirmedSave("count did not move")])
        rc, _g, cs, _c = self._run(fq, create_shift=boom, counts=(0, 5))
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
        self.refresh.assert_not_called()


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


class PublishMessage(unittest.TestCase):
    def test_lists_open_shifts_by_day(self):
        msg = w.publish_message(datetime.date(2026, 10, 12), [
            {"date": datetime.date(2026, 10, 17), "start_min": 870, "end_min": 1200},
            {"date": datetime.date(2026, 10, 13), "start_min": 900, "end_min": 1230},
        ])
        self.assertIn("The schedule for **Oct 12–18** is now published in ADP", msg)
        self.assertIn("work with your availability", msg)
        self.assertIn("**Open shifts this week**\n\n- Tue Oct 13", msg)
        self.assertIn("claim it in the ADP app", msg)
        self.assertLess(msg.index("Tue Oct 13 · 3:00 PM – 8:30 PM"), msg.index("Sat Oct 17 · 2:30 PM – 8:00 PM"))

    def test_no_open_shifts_and_month_boundary(self):
        msg = w.publish_message(datetime.date(2026, 10, 26), [])
        self.assertIn("Oct 26–Nov 1", msg)
        self.assertIn("no open shifts", msg)
        self.assertNotIn("claim it", msg)
