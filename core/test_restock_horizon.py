"""Unit tests for core/restock_horizon.py (pure planner + ensure wiring)."""

from __future__ import annotations

import datetime as dt
import unittest
from unittest import mock

from core.restock_horizon import (
    AUTOMATION_BY,
    ensure_restock_horizon,
    plan_horizon_additions,
)

MON = dt.date(2026, 10, 19)
FRI = dt.date(2026, 10, 23)


def plan(**kw):
    kw.setdefault("today", MON)
    kw.setdefault("weekday", "friday")
    kw.setdefault("horizon", 2)
    kw.setdefault("scheduled", [])
    return plan_horizon_additions(**kw)


class TestPlanHorizonAdditions(unittest.TestCase):
    def test_off_when_weekday_unset(self):
        self.assertEqual(plan(weekday=None), [])

    def test_off_when_weekday_unknown(self):
        self.assertEqual(plan(weekday="funday"), [])

    def test_off_when_horizon_zero(self):
        self.assertEqual(plan(horizon=0), [])

    def test_fills_two_fridays_from_empty(self):
        self.assertEqual(plan(), [FRI, dt.date(2026, 10, 30)])

    def test_tops_up_one_when_one_friday_live(self):
        self.assertEqual(plan(weekday="Friday", scheduled=[FRI]), [dt.date(2026, 10, 30)])

    def test_noop_when_already_two(self):
        self.assertEqual(plan(scheduled=[FRI, dt.date(2026, 10, 30)]), [])

    def test_ignores_past_scheduled_dates(self):
        self.assertEqual(plan(scheduled=[dt.date(2026, 10, 16)]), [FRI, dt.date(2026, 10, 30)])

    def test_skips_week_with_thursday_exception(self):
        self.assertEqual(plan(scheduled=[dt.date(2026, 10, 22)]), [dt.date(2026, 10, 30)])

    def test_includes_today_when_today_is_delivery_weekday(self):
        self.assertEqual(plan(today=FRI), [FRI, dt.date(2026, 10, 30)])

    def test_delivery_day_after_closing_adds_week_after_next(self):
        # Friday evening: today's closing is in, so 10-23 no longer counts as live
        # and the planner must not re-propose it — the next order is for 11-06.
        got = plan(today=FRI, today_closed=True, scheduled=[FRI, dt.date(2026, 10, 30)])
        self.assertEqual(got, [dt.date(2026, 11, 6)])

    def test_never_readds_operator_removed_date(self):
        got = plan(scheduled=[FRI], removed=[dt.date(2026, 10, 30)])
        self.assertEqual(got, [dt.date(2026, 11, 6)])

    def test_delayed_delivery_moved_to_next_week_keeps_that_weeks_friday(self):
        # Fri 10-23 slipped to Tue 10-27 via Move (10-23 cleared). The regular
        # Fri 10-30 still happens, so it is the second date.
        got = plan(
            today=dt.date(2026, 10, 24), scheduled=[dt.date(2026, 10, 27)], removed=[FRI],
        )
        self.assertEqual(got, [dt.date(2026, 10, 30)])

    def test_delay_registered_without_move_counts_from_nearest_friday(self):
        # Mon 10-26 registered on its own; nearest Friday is the past 10-23.
        got = plan(today=dt.date(2026, 10, 24), scheduled=[dt.date(2026, 10, 26)])
        self.assertEqual(got, [dt.date(2026, 10, 30)])

    def test_holiday_moved_earlier_does_not_readd_friday(self):
        # Fri 10-30 moved to Thu 10-29: second date is the following Friday.
        got = plan(
            scheduled=[FRI, dt.date(2026, 10, 29)], removed=[dt.date(2026, 10, 30)], horizon=3,
        )
        self.assertEqual(got, [dt.date(2026, 11, 6)])

    def test_both_live_dates_moved_fills_from_next_free_friday(self):
        # 10-23 → Mon 10-26 and 10-30 → Mon 11-02 (supplier running late twice).
        got = plan(
            today=dt.date(2026, 10, 24),
            scheduled=[dt.date(2026, 10, 26)],
            removed=[FRI, dt.date(2026, 10, 30)],
        )
        self.assertEqual(got, [dt.date(2026, 11, 6)])

    def test_off_disables(self):
        self.assertEqual(plan(weekday="off"), [])


class TestEnsureRestockHorizon(unittest.TestCase):
    def _cfg(self, get_cfg):
        get_cfg.side_effect = lambda store, key: {
            "restock_delivery_weekday": "friday",
            "restock_horizon_dates": "2",
        }.get(key)

    @mock.patch("core.restock_horizon._log_schedule_set")
    @mock.patch("core.restock_horizon._insert_schedule_if_absent", return_value=True)
    @mock.patch("core.restock_horizon._horizon_inputs")
    @mock.patch("core.datastore.get_client", return_value=object())
    @mock.patch("core.store_config.get_config")
    def test_ensure_inserts_and_logs_planned(self, get_cfg, _client, inputs, insert, log_set):
        self._cfg(get_cfg)
        inputs.return_value = {"today": MON, "today_closed": False, "scheduled": [], "removed": []}
        added = ensure_restock_horizon("palmetto")
        self.assertEqual(added, ["2026-10-23", "2026-10-30"])
        self.assertEqual(insert.call_count, 2)
        self.assertEqual(log_set.call_count, 2)
        self.assertEqual(insert.call_args.args[3], AUTOMATION_BY)

    @mock.patch("core.restock_horizon._log_schedule_set")
    @mock.patch("core.restock_horizon._insert_schedule_if_absent", return_value=False)
    @mock.patch("core.restock_horizon._horizon_inputs")
    @mock.patch("core.datastore.get_client", return_value=object())
    @mock.patch("core.store_config.get_config")
    def test_ensure_skips_log_when_row_already_present(self, get_cfg, _client, inputs, _insert, log_set):
        self._cfg(get_cfg)
        inputs.return_value = {"today": MON, "today_closed": False, "scheduled": [], "removed": []}
        self.assertEqual(ensure_restock_horizon("palmetto"), [])
        log_set.assert_not_called()

    @mock.patch("core.restock_horizon._insert_schedule_if_absent", side_effect=RuntimeError("boom"))
    @mock.patch("core.restock_horizon._horizon_inputs")
    @mock.patch("core.datastore.get_client", return_value=object())
    @mock.patch("core.store_config.get_config")
    def test_insert_failure_leaves_breadcrumb_not_raise(self, get_cfg, _client, inputs, _insert):
        self._cfg(get_cfg)
        inputs.return_value = {"today": MON, "today_closed": False, "scheduled": [FRI], "removed": []}
        with self.assertLogs("core.restock_horizon", level="ERROR") as logs:
            self.assertEqual(ensure_restock_horizon("palmetto"), [])
        self.assertIn("restock_horizon_insert_failed store=palmetto date=2026-10-30", logs.output[0])

    @mock.patch("core.restock_horizon._insert_schedule_if_absent")
    @mock.patch("core.store_config.get_config", return_value=None)
    def test_ensure_noop_when_unset(self, _cfg, insert):
        self.assertEqual(ensure_restock_horizon("palmetto"), [])
        insert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
