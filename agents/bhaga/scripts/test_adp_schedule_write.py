"""adp_schedule_write orchestration (Issue #337) — BQ and ADP are faked."""
from __future__ import annotations

import contextlib
import datetime
import unittest
from collections import Counter
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


class NotifyPublished(unittest.TestCase):
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
        notify.assert_called_once_with("palmetto", self.W, fresh=True)

    def test_adp_sign_in_failure_still_sends_flagged_stale(self):
        def broken(**_kw):
            raise RuntimeError("login")

        rc, notify = self._run(session=broken)
        self.assertEqual(rc, 0)
        notify.assert_called_once_with("palmetto", self.W, fresh=False)

    def test_dm_failure_fails_the_job(self):
        rc, _notify = self._run(sent=False)
        self.assertEqual(rc, 1)
