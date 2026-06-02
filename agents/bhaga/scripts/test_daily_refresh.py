#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.daily_refresh.

Run:
    python3 agents/bhaga/scripts/test_daily_refresh.py

Covers:

  1. ``compute_gap_window`` — Layer C of the seamless_bhaga_refresh fix:
     refuses to silently fall back to "fresh install" when the
     ``data_window_end`` config cell is non-empty but unparseable,
     because doing so would burn ~60 days of Square API budget plus a
     fresh 2FA challenge.

  2. ``_run_state_dir`` / ``step_already_done`` / ``mark_step_done`` —
     marker-dir keying by ``refresh_date`` (NOT today_ct). Recovery
     runs for past dates must not pollute today's marker namespace.

  3. ``is_refresh_date_complete`` — completeness gate boundaries
     (past day, today-pre-21:00, today-at-21:00, today-post-21:00,
     future). Boundary inclusive at 21:00 CT.

  4. ``main()`` hard-refuse path — invoking with --date <today> at
     13:00 CT raises SystemExit; --date <yesterday> does not.

These tests target the pure extracted functions plus an argv-mocked
``main()`` — no Sheets, Playwright, or Slack side effects.
"""

from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

import types

from agents.bhaga.scripts import daily_refresh
from agents.bhaga.scripts.daily_refresh import (
    CT,
    _SHOP_CLOSE_BUFFER_HOUR_CT,
    _recover_stale_downstream_markers,
    _run_state_dir,
    clear_step_done,
    compute_gap_window,
    is_refresh_date_complete,
    mark_step_done,
    step_already_done,
)


class ComputeGapWindowTests(unittest.TestCase):
    DATA_START = datetime.date(2026, 3, 22)
    REFRESH_DATE = datetime.date(2026, 5, 21)

    def test_empty_cell_triggers_fresh_install(self):
        gap_start, gap_source = compute_gap_window(
            prev_end=None,
            cell_was_empty=True,
            data_start=self.DATA_START,
            refresh_date=self.REFRESH_DATE,
        )
        self.assertEqual(gap_start, self.DATA_START)
        self.assertTrue(
            gap_source.startswith("fresh install"),
            f"gap_source={gap_source!r} should start with 'fresh install'",
        )

    def test_unparseable_cell_raises_systemexit(self):
        with self.assertRaises(SystemExit) as cm:
            compute_gap_window(
                prev_end=None,
                cell_was_empty=False,
                data_start=self.DATA_START,
                refresh_date=self.REFRESH_DATE,
            )
        # The phrase below MUST stay in the error message — future
        # maintainers grep for it when investigating why a Square scrape
        # was aborted.
        self.assertIn("60-day Square re-scrape", str(cm.exception))

    def test_serial_recovered_yields_incremental_gap(self):
        # Layer B coerced "46162" -> date(2026, 5, 20) BEFORE we get here.
        prev_end = datetime.date(2026, 5, 20)
        gap_start, gap_source = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=False,
            data_start=self.DATA_START,
            refresh_date=self.REFRESH_DATE,
        )
        self.assertEqual(gap_start, datetime.date(2026, 5, 21))
        self.assertIn("2026-05-20", gap_source)
        self.assertIn("+ 1", gap_source)

    def test_iso_normal_incremental(self):
        # Routine nightly run — yesterday's window end + 1 = today.
        prev_end = datetime.date(2026, 5, 20)
        gap_start, gap_source = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=False,
            data_start=self.DATA_START,
            refresh_date=datetime.date(2026, 5, 21),
        )
        self.assertEqual(gap_start, datetime.date(2026, 5, 21))
        self.assertEqual(gap_source, "sheet.data_window_end=2026-05-20 + 1")

    def test_caught_up_yesterday_means_empty_gap(self):
        # prev_end == refresh_date → gap_start lands a day AFTER refresh_date,
        # which the caller interprets as "nothing to scrape".
        prev_end = datetime.date(2026, 5, 21)
        gap_start, _ = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=False,
            data_start=self.DATA_START,
            refresh_date=datetime.date(2026, 5, 21),
        )
        self.assertGreater(gap_start, datetime.date(2026, 5, 21))


class RunStateDirKeyingTests(unittest.TestCase):
    """The marker dir MUST be keyed by refresh_date, not today_ct.

    Recovery scenario that motivated the fix (2026-05-21 13:20 CT):
      Operator runs `--date 2026-05-20`. Markers should land under
      run-2026-05-20/, NOT run-2026-05-21/. The previous keying
      collided with the upcoming 21:00 CT cron's namespace and either
      (a) caused the cron to skip everything, or (b) caused the
      recovery run to re-pull today's partial data.
    """

    def test_run_state_dir_uses_refresh_date_yesterday(self):
        d = _run_state_dir(datetime.date(2026, 5, 20))
        self.assertTrue(
            str(d).endswith("run-2026-05-20"),
            f"expected path to end with 'run-2026-05-20', got {d}",
        )

    def test_run_state_dir_uses_refresh_date_today(self):
        d = _run_state_dir(datetime.date(2026, 5, 21))
        self.assertTrue(
            str(d).endswith("run-2026-05-21"),
            f"expected path to end with 'run-2026-05-21', got {d}",
        )

    def test_run_state_dir_different_dates_yield_different_paths(self):
        a = _run_state_dir(datetime.date(2026, 5, 20))
        b = _run_state_dir(datetime.date(2026, 5, 21))
        self.assertNotEqual(a, b)

    def test_mark_and_check_use_passed_refresh_date_not_today(self):
        # Sandbox HOME so the test doesn't pollute the operator's real
        # ~/.bhaga/state/ dir.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd_past = datetime.date(2026, 5, 20)
                rd_other = datetime.date(2026, 5, 21)
                self.assertFalse(step_already_done(rd_past, "consolidate_csv"))
                mark_step_done(rd_past, "consolidate_csv", note="test")
                self.assertTrue(step_already_done(rd_past, "consolidate_csv"))
                # The marker for refresh_date=2026-05-20 must NOT be
                # visible to refresh_date=2026-05-21 (the namespaces are
                # independent — this is the whole point of the fix).
                self.assertFalse(step_already_done(rd_other, "consolidate_csv"))


class IsRefreshDateCompleteTests(unittest.TestCase):
    """Boundary tests for the completeness gate.

    `now_ct` is injected so tests are stable regardless of when the
    suite is run (don't depend on wall-clock).
    """

    TODAY = datetime.date(2026, 5, 21)
    YESTERDAY = datetime.date(2026, 5, 20)
    TOMORROW = datetime.date(2026, 5, 22)

    def _now(self, hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
        return datetime.datetime(
            self.TODAY.year, self.TODAY.month, self.TODAY.day,
            hour, minute, second, tzinfo=CT,
        )

    def test_past_date_is_complete(self):
        # Mid-day "now" — past dates are still complete regardless.
        self.assertTrue(
            is_refresh_date_complete(self.YESTERDAY, now_ct=self._now(13, 0))
        )

    def test_today_at_13_00_is_incomplete(self):
        self.assertFalse(
            is_refresh_date_complete(self.TODAY, now_ct=self._now(13, 0))
        )

    def test_today_at_20_59_is_incomplete(self):
        self.assertFalse(
            is_refresh_date_complete(self.TODAY, now_ct=self._now(20, 59, 59))
        )

    def test_today_at_21_00_boundary_is_complete(self):
        # This is the canonical nightly-cron firing time. The cron
        # invokes daily_refresh with --date today_ct; the gate MUST
        # pass at 21:00 sharp or we just broke the production cron.
        self.assertTrue(
            is_refresh_date_complete(self.TODAY, now_ct=self._now(21, 0))
        )

    def test_today_at_23_00_is_complete(self):
        self.assertTrue(
            is_refresh_date_complete(self.TODAY, now_ct=self._now(23, 0))
        )

    def test_future_date_is_incomplete(self):
        # Even after the buffer hour, a future date is incomplete.
        self.assertFalse(
            is_refresh_date_complete(self.TOMORROW, now_ct=self._now(23, 59))
        )

    def test_buffer_constant_is_21(self):
        # Pin the boundary so a future tweak to the constant forces a
        # cron schedule review at the same time.
        self.assertEqual(_SHOP_CLOSE_BUFFER_HOUR_CT, 21)


class MainHardRefuseTests(unittest.TestCase):
    """`main()` must SystemExit when refresh_date is in-progress.

    We patch the wall-clock so the test pins now_ct to 2026-05-21 13:00
    CT (the same hour the bug surfaced in production). We also patch
    argv so --date can be tested without touching real arg parsing.

    The gate runs BEFORE any data fetches, so no Sheets / Playwright /
    Slack patches are needed — but we patch _today_ct defensively in
    case main() ever inspects it pre-gate.
    """

    NOW = datetime.datetime(2026, 5, 21, 13, 0, 0, tzinfo=CT)
    TODAY_ISO = "2026-05-21"
    YESTERDAY_ISO = "2026-05-20"

    # Sentinel raised by the patched _load_profile when the gate passes.
    # Lets us distinguish "gate passed and main() proceeded" (we hit the
    # sentinel) from "gate failed" (we hit SystemExit with the gate
    # message) WITHOUT ever touching real Sheets / Playwright / Slack.
    class _PostGateSentinel(Exception):
        pass

    def _run_main_with(self, date_arg: str):
        argv = ["daily_refresh", "--store", "palmetto", "--date", date_arg,
                "--no-slack", "--skip-square", "--skip-timecard",
                "--skip-reviews", "--skip-model"]

        # Patch datetime.datetime.now(CT) to return self.NOW. Because
        # `datetime.datetime` is C-implemented and its `now` classmethod
        # cannot be patched directly, swap the datetime CLASS the module
        # bound at import time with a subclass that pins `now()`. We
        # leave all other datetime APIs untouched.
        class _FixedNowDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return self.NOW if tz is not None else self.NOW.replace(tzinfo=None)

        def _explode(*_args, **_kwargs):
            # Sentinel for the yesterday-path: prove the gate passed by
            # raising as soon as main() tries to touch the store profile.
            # Any work past the gate goes through _load_profile first.
            raise self._PostGateSentinel(
                "gate passed; _load_profile was reached"
            )

        return _patch_then_run(
            self, _FixedNowDateTime, argv, _explode,
        )

    def test_today_at_13_00_raises_systemexit(self):
        with self.assertRaises(SystemExit) as cm:
            self._run_main_with(self.TODAY_ISO)
        msg = str(cm.exception)
        self.assertIn("not yet complete", msg)
        self.assertIn(self.TODAY_ISO, msg)

    def test_yesterday_passes_gate(self):
        # Yesterday is past → complete → gate passes. The patched
        # _load_profile raises a sentinel as soon as main() steps past
        # the gate, so we never run any real Sheets / Playwright work.
        # Test passes iff the SystemExit (gate) is NOT raised AND the
        # sentinel IS raised. Anything else is a regression.
        with self.assertRaises(self._PostGateSentinel):
            self._run_main_with(self.YESTERDAY_ISO)


def _patch_then_run(test_self, fixed_dt_cls, argv, load_profile_stub):
    """Helper shared by MainHardRefuseTests cases.

    Centralizes the patch stack so the test bodies stay focused on the
    boundary they're asserting. The patches:
      * daily_refresh.datetime.datetime  → fixed-now subclass
      * daily_refresh._load_profile      → raise post-gate sentinel
      * sys.argv                         → caller-supplied
    """
    with mock.patch.object(daily_refresh.datetime, "datetime", fixed_dt_cls), \
         mock.patch.object(daily_refresh, "_load_profile", side_effect=load_profile_stub), \
         mock.patch.object(sys, "argv", argv):
        return daily_refresh.main()


class ClearStepDoneTests(unittest.TestCase):
    """clear_step_done passthrough invalidates a marker (local backend)."""

    def test_clear_removes_marker_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                mark_step_done(rd, "write_raw_sheets")
                self.assertTrue(step_already_done(rd, "write_raw_sheets"))
                clear_step_done(rd, "write_raw_sheets")
                self.assertFalse(step_already_done(rd, "write_raw_sheets"))
                # Idempotent — clearing an absent marker is a no-op.
                clear_step_done(rd, "write_raw_sheets")
                self.assertFalse(step_already_done(rd, "write_raw_sheets"))


class RecoverStaleDownstreamMarkersTests(unittest.TestCase):
    """The 2026-05-31 fix: when a previously-failed OTP portal recovers with
    fresh data while downstream markers are already done from a prior partial
    run, invalidate those markers so they recompute. Always on (no feature
    flag) — safe by construction (idempotent upserts + post-condition guard)."""

    DOWNSTREAM = ("write_raw_sheets", "update_model_sheet", "process_reviews")

    def _ok(self):
        return types.SimpleNamespace(success=True)

    def _failed(self):
        return types.SimpleNamespace(success=False)

    def _seed_downstream_done(self, rd):
        for step in self.DOWNSTREAM:
            mark_step_done(rd, step)

    def test_clears_stale_markers_when_portal_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                self._seed_downstream_done(rd)
                cleared = _recover_stale_downstream_markers(
                    rd, {"square": self._ok()}, dry_run=False
                )
                self.assertEqual(set(cleared), set(self.DOWNSTREAM))
                for step in self.DOWNSTREAM:
                    self.assertFalse(step_already_done(rd, step))

    def test_no_stale_markers_is_noop(self):
        """A normal first run (downstream not yet done) clears nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                cleared = _recover_stale_downstream_markers(
                    rd, {"square": self._ok()}, dry_run=False
                )
                self.assertEqual(cleared, [])

    def test_portal_failed_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                self._seed_downstream_done(rd)
                cleared = _recover_stale_downstream_markers(
                    rd, {"square": self._failed()}, dry_run=False
                )
                self.assertEqual(cleared, [])
                for step in self.DOWNSTREAM:
                    self.assertTrue(step_already_done(rd, step))

    def test_dry_run_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                self._seed_downstream_done(rd)
                cleared = _recover_stale_downstream_markers(
                    rd, {"square": self._ok()}, dry_run=True
                )
                self.assertEqual(cleared, [])
                for step in self.DOWNSTREAM:
                    self.assertTrue(step_already_done(rd, step))

    def test_partial_clear_failure_reports_only_cleared(self):
        """If one clear_step_done raises mid-loop, the breadcrumb + return value
        must report only the steps ACTUALLY cleared — never overstate a full
        recovery (the breadcrumb principle)."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                self._seed_downstream_done(rd)
                real_clear = daily_refresh.clear_step_done

                def flaky(refresh_date, step):
                    if step == "update_model_sheet":
                        raise RuntimeError("firestore unavailable")
                    return real_clear(refresh_date, step)

                with mock.patch.object(daily_refresh, "clear_step_done", side_effect=flaky):
                    cleared = _recover_stale_downstream_markers(
                        rd, {"square": self._ok()}, dry_run=False
                    )
                self.assertEqual(set(cleared), {"write_raw_sheets", "process_reviews"})
                self.assertNotIn("update_model_sheet", cleared)
                # The step we couldn't clear is still present (will short-circuit).
                self.assertTrue(step_already_done(rd, "update_model_sheet"))
                self.assertFalse(step_already_done(rd, "write_raw_sheets"))

    def test_adp_recovery_also_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                self._seed_downstream_done(rd)
                cleared = _recover_stale_downstream_markers(
                    rd, {"adp": self._ok()}, dry_run=False
                )
                self.assertEqual(set(cleared), set(self.DOWNSTREAM))


class PreflightBrowserTests(unittest.TestCase):
    """The pre-flight smoke test is best-effort and never raises."""

    def test_healthy_returns_true(self):
        with mock.patch(
            "skills._browser_runtime.runtime.browser_healthcheck",
            return_value=True,
        ):
            self.assertTrue(daily_refresh._preflight_browser_ok())

    def test_unhealthy_returns_false_non_fatal(self):
        with mock.patch(
            "skills._browser_runtime.runtime.browser_healthcheck",
            return_value=False,
        ):
            self.assertFalse(daily_refresh._preflight_browser_ok())


class LatestClosedPeriodWithEarningsTests(unittest.TestCase):
    """The cadence-safe probe: require adp reconciliation only when the GCS
    cache actually holds a covering Earnings export for the latest closed period."""

    PROFILE = {"adp_run": {"pay_periods_anchor_end_date": "2026-05-17",
                           "pay_frequency": "biweekly"}}

    def _patch_ums(self, *, period, actuals):
        from agents.bhaga.scripts import update_model_sheet
        return mock.patch.multiple(
            update_model_sheet,
            most_recent_closed_period=mock.Mock(return_value=period),
            load_cc_tips_earnings_from_gcs=mock.Mock(return_value=[{"x": 1}]),
            actual_cc_tips_by_period=mock.Mock(return_value=actuals),
        )

    def test_returns_period_when_earnings_cover_it(self):
        period = (datetime.date(2026, 5, 18), datetime.date(2026, 5, 31))
        with self._patch_ums(period=period,
                             actuals={("2026-05-18", "2026-05-31"): {"A": 100}}):
            out = daily_refresh._latest_closed_period_with_earnings(
                profile=self.PROFILE, store="palmetto",
                refresh_date=datetime.date(2026, 6, 2))
        self.assertEqual(out, ("2026-05-18", "2026-05-31"))

    def test_returns_none_when_no_covering_export(self):
        period = (datetime.date(2026, 5, 18), datetime.date(2026, 5, 31))
        with self._patch_ums(period=period, actuals={}):  # no export for it
            out = daily_refresh._latest_closed_period_with_earnings(
                profile=self.PROFILE, store="palmetto",
                refresh_date=datetime.date(2026, 6, 2))
        self.assertIsNone(out)

    def test_returns_none_without_anchor(self):
        out = daily_refresh._latest_closed_period_with_earnings(
            profile={"adp_run": {}}, store="palmetto",
            refresh_date=datetime.date(2026, 6, 2))
        self.assertIsNone(out)

    def test_soft_on_loader_error(self):
        from agents.bhaga.scripts import update_model_sheet
        with mock.patch.object(update_model_sheet, "most_recent_closed_period",
                               side_effect=RuntimeError("boom")):
            out = daily_refresh._latest_closed_period_with_earnings(
                profile=self.PROFILE, store="palmetto",
                refresh_date=datetime.date(2026, 6, 2))
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
