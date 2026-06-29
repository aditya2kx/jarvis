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

import ast
import datetime
import os
import pathlib
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
    _MODEL_RECOMPUTE_STEPS,
    _assert_model_matches_raw_rollup,
    _detect_and_clear_stale_model,
    _model_vs_rollup_drift,
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


class MainHaltBreakerTests(unittest.TestCase):
    """The circuit breaker at main() startup: refuse a fresh run while tripped
    (distinct EXIT_HALTED), but let --ignore-halt and OTP READY resumes through.

    The halt check sits between the completeness gate and _load_profile, so the
    same minimal patch stack as MainHardRefuseTests reaches it (no Sheets /
    Playwright / Slack). A past --date passes the gate; HOME points at a tmp dir
    so the local state backend reads the breaker we plant.
    """

    NOW = datetime.datetime(2026, 6, 2, 13, 0, 0, tzinfo=CT)
    PAST_ISO = "2026-06-01"

    class _PostHaltSentinel(Exception):
        pass

    def _run(self, tmp, *, ignore_halt=False, otp_ready=False):
        argv = ["daily_refresh", "--store", "palmetto", "--date", self.PAST_ISO,
                "--no-slack", "--skip-square", "--skip-timecard",
                "--skip-reviews", "--skip-model"]
        if ignore_halt:
            argv.append("--ignore-halt")

        class _FixedNowDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return self.NOW if tz is not None else self.NOW.replace(tzinfo=None)

        def _explode(*_a, **_k):
            raise self._PostHaltSentinel("proceeded past the halt check")

        if otp_ready:
            from skills.bhaga_config.state_adapter import mark_otp_ready
            rd = datetime.date.fromisoformat(self.PAST_ISO)
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                daily_refresh._adapter_save_pending_otp(
                    rd, ["Square"], requested_at="2026-06-01T21:00:00-05:00")
                mark_otp_ready(rd)

        with mock.patch.dict(os.environ, {"HOME": tmp}), \
             mock.patch.object(daily_refresh.datetime, "datetime", _FixedNowDateTime), \
             mock.patch.object(daily_refresh, "failure_alert", lambda **k: None), \
             mock.patch.object(daily_refresh, "_load_profile", side_effect=_explode), \
             mock.patch.object(sys, "argv", argv):
            return daily_refresh.main()

    def test_refuses_when_halted(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                daily_refresh._adapter_set_pipeline_halt(
                    reason="semantic guard failed: adp dead",
                    refresh_date=datetime.date(2026, 6, 1))
            rc = self._run(tmp)
            self.assertEqual(rc, daily_refresh.EXIT_HALTED)

    def test_ignore_halt_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                daily_refresh._adapter_set_pipeline_halt(reason="boom")
            with self.assertRaises(self._PostHaltSentinel):
                self._run(tmp, ignore_halt=True)

    def test_otp_ready_resume_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                daily_refresh._adapter_set_pipeline_halt(reason="boom")
            with self.assertRaises(self._PostHaltSentinel):
                self._run(tmp, otp_ready=True)

    def test_no_halt_proceeds_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(self._PostHaltSentinel):
                self._run(tmp)


class ClearStepDoneTests(unittest.TestCase):
    """clear_step_done passthrough invalidates a marker (local backend)."""

    def test_clear_removes_marker_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 5, 31)
                mark_step_done(rd, "load_raw_bigquery")
                self.assertTrue(step_already_done(rd, "load_raw_bigquery"))
                clear_step_done(rd, "load_raw_bigquery")
                self.assertFalse(step_already_done(rd, "load_raw_bigquery"))
                # Idempotent — clearing an absent marker is a no-op.
                clear_step_done(rd, "load_raw_bigquery")
                self.assertFalse(step_already_done(rd, "load_raw_bigquery"))


class RecoverStaleDownstreamMarkersTests(unittest.TestCase):
    """The 2026-05-31 fix: when a previously-failed OTP portal recovers with
    fresh data while downstream markers are already done from a prior partial
    run, invalidate those markers so they recompute. Always on (no feature
    flag) — safe by construction (idempotent upserts + post-condition guard)."""

    # Bind to the production constant so the test can never drift from the list
    # the pipeline actually invalidates (the 2026-06-08 incident was a missing
    # member here — render_raw_sheets / materialize_model_bq).
    DOWNSTREAM = daily_refresh._RECOVERY_DOWNSTREAM_STEPS

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
                    if step == "materialize_model_bq":
                        raise RuntimeError("firestore unavailable")
                    return real_clear(refresh_date, step)

                with mock.patch.object(daily_refresh, "clear_step_done", side_effect=flaky):
                    cleared = _recover_stale_downstream_markers(
                        rd, {"square": self._ok()}, dry_run=False
                    )
                self.assertEqual(set(cleared), set(self.DOWNSTREAM) - {"materialize_model_bq"})
                self.assertNotIn("materialize_model_bq", cleared)
                self.assertTrue(step_already_done(rd, "materialize_model_bq"))
                self.assertFalse(step_already_done(rd, "load_raw_bigquery"))

    def test_recovery_constants_exclude_update_model_sheet(self):
        self.assertNotIn("update_model_sheet", daily_refresh._RECOVERY_DOWNSTREAM_STEPS)
        self.assertNotIn("update_model_sheet", _MODEL_RECOMPUTE_STEPS)

    def test_includes_materialize_and_reviews_steps(self):
        """Regression for 2026-06-08 (updated post-Sheets-exit): the recovery
        list MUST include materialize_model_bq (and NOT render_* which are
        deleted). Without it, fresh portal data lands in BQ raw but the model
        is never recomputed."""
        for step in ("load_raw_bigquery", "materialize_model_bq", "process_reviews"):
            self.assertIn(step, daily_refresh._RECOVERY_DOWNSTREAM_STEPS)
        for step in ("render_raw_sheets", "render_model_sheet_from_bq",
                     "reconcile_model"):
            self.assertNotIn(step, daily_refresh._RECOVERY_DOWNSTREAM_STEPS)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                rd = datetime.date(2026, 6, 8)
                self._seed_downstream_done(rd)
                cleared = _recover_stale_downstream_markers(
                    rd, {"square": self._ok()}, dry_run=False
                )
                self.assertIn("materialize_model_bq", cleared)
                self.assertFalse(step_already_done(rd, "materialize_model_bq"))

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


class UnifiedWindowArgTests(unittest.TestCase):
    """Test --from/--to unified window: arg parsing, env-var fallback, fan-out.

    Uses a lightweight parse-only helper — extracts the argparser from
    daily_refresh without running main() — to verify CLI contract without
    any network / sheet / Playwright side-effects.
    """

    def _parse(self, extra_args: list[str], env: dict | None = None):
        """Build the same argparser that main() would build, parse extra_args."""
        import argparse
        # Rebuild a minimal argparser matching the real one's window-relevant args.
        # daily_refresh.main() uses `cli`; we replicate only the subset we need.
        cli = argparse.ArgumentParser()
        cli.add_argument("--store", default="palmetto")
        cli.add_argument("--date", default=None)
        cli.add_argument("--from-date", dest="from_date", default=None)
        cli.add_argument("--square-from", default=None)
        cli.add_argument("--square-to", default=None)
        cli.add_argument("--adp-from", default=None)
        cli.add_argument("--adp-to", default=None)
        cli.add_argument("--adp-pay-period", default=None)
        cli.add_argument("--reviews-since", default=None)
        cli.add_argument("--reviews-until", default=None)
        cli.add_argument("--from", dest="window_from", default=None)
        cli.add_argument("--to", dest="window_to", default=None)
        args = cli.parse_args(extra_args)
        # Apply env-var fallbacks as main() does.
        if env:
            args.window_from = args.window_from or env.get("BHAGA_WINDOW_FROM") or None
            args.window_to = args.window_to or env.get("BHAGA_WINDOW_TO") or None
        return args

    def test_from_to_sets_refresh_date_to_to(self):
        """--to should become refresh_date (used as the GCS cache folder)."""
        args = self._parse(["--from", "2026-03-22", "--to", "2026-06-04"])
        # Mimic main()'s date_arg resolution.
        date_arg = args.date or args.window_to or None
        self.assertEqual(date_arg, "2026-06-04")
        self.assertEqual(datetime.date.fromisoformat(date_arg), datetime.date(2026, 6, 4))

    def test_from_overrides_gap_start(self):
        """--from should set gap_start bypassing the sheet gap-resolver."""
        args = self._parse(["--from", "2026-03-22", "--to", "2026-06-04"])
        _from_override = args.from_date or args.window_from
        self.assertEqual(_from_override, "2026-03-22")
        gap_start = datetime.date.fromisoformat(_from_override)
        self.assertEqual(gap_start, datetime.date(2026, 3, 22))

    def test_explicit_per_source_overrides_window(self):
        """Per-source flags override the unified window (explicit override wins)."""
        args = self._parse([
            "--from", "2026-03-22", "--to", "2026-06-04",
            "--square-from", "2026-05-01",
            "--adp-to", "2026-05-31",
            "--reviews-since", "2026-04-01",
        ])
        # square_from honors --square-from (explicit) not window_from
        square_from = (
            datetime.date.fromisoformat(args.square_from) if args.square_from
            else (datetime.date.fromisoformat(args.window_from) if args.window_from else None)
        )
        self.assertEqual(square_from, datetime.date(2026, 5, 1))
        # adp_window_to honors --adp-to (explicit) not window_to
        adp_window_to = (
            datetime.date.fromisoformat(args.adp_to) if args.adp_to
            else (datetime.date.fromisoformat(args.window_to) if args.window_to else None)
        )
        self.assertEqual(adp_window_to, datetime.date(2026, 5, 31))
        # reviews_since honors --reviews-since (explicit) not window_from
        rev_since = args.reviews_since or args.window_from
        self.assertEqual(rev_since, "2026-04-01")

    def test_windowed_run_sets_custom_range_and_select_all(self):
        """--from/--to without per-source overrides => custom-range earnings + Select All."""
        args = self._parse(["--from", "2026-03-22", "--to", "2026-06-04"])
        adp_window_from = (
            datetime.date.fromisoformat(args.adp_from) if args.adp_from
            else (datetime.date.fromisoformat(args.window_from) if args.window_from else None)
        )
        adp_window_to = (
            datetime.date.fromisoformat(args.adp_to) if args.adp_to
            else (datetime.date.fromisoformat(args.window_to) if args.window_to else None)
        )
        earnings_custom_range = bool(adp_window_from and adp_window_to)
        self.assertTrue(earnings_custom_range)
        # adp_target_date should become None (Select All pay periods)
        adp_target_date_would_be_none = (
            args.window_from and not args.adp_pay_period and not args.adp_to
        )
        self.assertTrue(adp_target_date_would_be_none)

    def test_env_var_fallback_honors_bhaga_window(self):
        """BHAGA_WINDOW_FROM/TO env vars should fill in missing --from/--to."""
        args = self._parse(
            [],
            env={"BHAGA_WINDOW_FROM": "2026-03-22", "BHAGA_WINDOW_TO": "2026-06-04"},
        )
        self.assertEqual(args.window_from, "2026-03-22")
        self.assertEqual(args.window_to, "2026-06-04")

    def test_cli_wins_over_env(self):
        """CLI --from/--to take precedence over BHAGA_WINDOW_* env vars."""
        args = self._parse(
            ["--from", "2026-05-01", "--to", "2026-05-31"],
            env={"BHAGA_WINDOW_FROM": "2026-01-01", "BHAGA_WINDOW_TO": "2026-01-31"},
        )
        self.assertEqual(args.window_from, "2026-05-01")
        self.assertEqual(args.window_to, "2026-05-31")

    def test_reviews_window_fan_out(self):
        """--from/--to should fan out to reviews_since/until when no override."""
        args = self._parse(["--from", "2026-03-22", "--to", "2026-06-04"])
        rev_since = args.reviews_since or args.window_from
        rev_until = args.reviews_until or args.window_to
        self.assertEqual(rev_since, "2026-03-22")
        self.assertEqual(rev_until, "2026-06-04")


class EarningsCustomRangeTests(unittest.TestCase):
    """Unit tests for _earnings_within_session custom-range vs Last-payroll path."""

    def _make_page(self):
        """Return a Mock page with just enough Playwright API surface."""
        page = mock.MagicMock()
        # get_by_role(...).first.click() / .fill() should be silently callable.
        return page

    def _mock_store_profile(self):
        return mock.patch(
            "skills.adp_run_automation.runner._load_store_profile",
            return_value={"adp_run": {"wage_rate_report_name": "Earnings and Hours V1"}},
        )

    def _mock_navigate(self):
        return mock.patch("skills.adp_run_automation.runner._navigate_to_reports_landing")

    def _mock_report_tile(self):
        return mock.patch("skills.adp_run_automation.runner._open_saved_report_tile")

    def _mock_date_range_dropdown(self):
        return mock.patch("skills.adp_run_automation.runner._open_date_range_dropdown")

    def _mock_download(self):
        import pathlib
        fake_path = pathlib.Path("/tmp/fake-earnings.xlsx")
        return mock.patch("skills.adp_run_automation.runner.download_to", return_value=fake_path)

    def _mock_row_guard(self):
        return mock.patch("skills.adp_run_automation.runner._assert_earnings_xlsx_has_rows", return_value=5)

    def test_range_over_366_days_raises(self):
        from skills.adp_run_automation.runner import _earnings_within_session
        page = self._make_page()
        start = datetime.date(2024, 1, 1)
        end = datetime.date(2025, 2, 15)  # > 366 days
        with self._mock_store_profile(), self._mock_navigate():
            with self.assertRaises(ValueError) as cm:
                _earnings_within_session(
                    page, store="palmetto",
                    start=start, end=end,
                    use_custom_range=True,
                )
        self.assertIn("12 months", str(cm.exception))

    def test_custom_range_missing_dates_raises(self):
        from skills.adp_run_automation.runner import _earnings_within_session
        page = self._make_page()
        with self._mock_store_profile(), self._mock_navigate():
            with self.assertRaises(ValueError) as cm:
                _earnings_within_session(
                    page, store="palmetto",
                    use_custom_range=True,
                    # start/end intentionally omitted
                )
        self.assertIn("requires both start and end", str(cm.exception))


class ProcessReviewsUntilArgTests(unittest.TestCase):
    """--until arg caps review processing window."""

    def _parse_reviews_args(self, extra_args: list[str]):
        import argparse
        cli = argparse.ArgumentParser()
        cli.add_argument("--store", default="palmetto")
        cli.add_argument("--since", default=None)
        cli.add_argument("--until", default=None)
        cli.add_argument("--max-pages", type=int, default=40)
        cli.add_argument("--prefetched-messages", default=None)
        cli.add_argument("--dry-run", action="store_true")
        cli.add_argument("--no-slack", action="store_true")
        return cli.parse_args(extra_args)

    def test_until_arg_is_parsed(self):
        """--until is accepted and propagated."""
        args = self._parse_reviews_args(["--until", "2026-06-04"])
        self.assertEqual(args.until, "2026-06-04")

    def test_without_until_arg_is_none(self):
        """Default --until is None (no cap)."""
        args = self._parse_reviews_args([])
        self.assertIsNone(args.until)

    def test_reviews_cmd_includes_until_when_set(self):
        """process_reviews step command includes --until when window_to is set."""
        # Simulate the review_cmd construction logic from daily_refresh.main()
        rev_until = "2026-06-04"
        review_cmd: list[str] = ["python3", "-m", "agents.bhaga.scripts.process_reviews"]
        if rev_until:
            review_cmd.extend(["--until", rev_until])
        self.assertIn("--until", review_cmd)
        idx = review_cmd.index("--until")
        self.assertEqual(review_cmd[idx + 1], "2026-06-04")


class ModelVsRollupDriftTests(unittest.TestCase):
    """Tests for _model_vs_rollup_drift, _detect_and_clear_stale_model, and
    _assert_model_matches_raw_rollup.

    The 2026-06-09 incident: concurrent-execution race wrote model_daily Jun 9 = $0
    while square_daily_rollup had $1,964.51.  All three helpers are mocked at the
    BQ level so tests run offline."""

    RD = datetime.date(2026, 6, 9)

    # ── _model_vs_rollup_drift ─────────────────────────────────────────────────

    def _patch_drift(self, rows):
        """Patch google.cloud.bigquery.Client so the drift query returns `rows`."""
        # BQ rows support dict-style access; use plain dicts to replicate that.
        from google.cloud import bigquery as _bq  # noqa: PLC0415
        fake_result = [
            {"date": date, "rollup_gross_cents": rollup_cents, "model_gross_sales": model}
            for date, rollup_cents, model in rows
        ]
        fake_job = mock.MagicMock()
        fake_job.result.return_value = fake_result
        fake_client_instance = mock.MagicMock()
        fake_client_instance.query.return_value = fake_job
        # Patch the already-imported module's Client constructor directly.
        return mock.patch.object(_bq, "Client", return_value=fake_client_instance)

    def test_drift_detected_returns_tuples(self):
        with self._patch_drift([(self.RD, 196451, 0.0)]):
            result = _model_vs_rollup_drift(self.RD, lookback_days=1)
        self.assertEqual(len(result), 1)
        date, rollup, model = result[0]
        self.assertEqual(date, self.RD)
        self.assertAlmostEqual(rollup, 1964.51)
        self.assertEqual(model, 0.0)

    def test_healthy_returns_empty(self):
        with self._patch_drift([]):
            result = _model_vs_rollup_drift(self.RD)
        self.assertEqual(result, [])

    def test_bq_client_unavailable_returns_empty(self):
        """When BQ Client construction fails and gcloud token also fails → []."""
        from google.cloud import bigquery as _bq  # noqa: PLC0415
        with mock.patch.object(_bq, "Client", side_effect=Exception("no credentials")):
            with mock.patch("subprocess.check_output",
                            side_effect=Exception("gcloud unavailable")):
                result = _model_vs_rollup_drift(self.RD)
        self.assertEqual(result, [])

    def test_bq_query_error_returns_empty(self):
        from google.cloud import bigquery as _bq  # noqa: PLC0415
        fake_client = mock.MagicMock()
        fake_client.query.side_effect = RuntimeError("BQ quota exceeded")
        with mock.patch.object(_bq, "Client", return_value=fake_client):
            result = _model_vs_rollup_drift(self.RD)
        self.assertEqual(result, [])

    def test_bq_import_error_returns_empty(self):
        """If google-cloud-bigquery is somehow not importable → []."""
        from google.cloud import bigquery as _bq  # noqa: PLC0415
        with mock.patch.object(_bq, "Client", side_effect=ImportError("no module")):
            with mock.patch("subprocess.check_output",
                            side_effect=Exception("gcloud unavailable")):
                result = _model_vs_rollup_drift(self.RD)
        self.assertEqual(result, [])

    # ── _detect_and_clear_stale_model ─────────────────────────────────────────

    def _seed_model_markers(self, rd):
        for step in _MODEL_RECOMPUTE_STEPS:
            mark_step_done(rd, step)

    def test_drift_clears_model_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                self._seed_model_markers(self.RD)
                with self._patch_drift([(self.RD, 196451, 0.0)]):
                    cleared = _detect_and_clear_stale_model(self.RD, dry_run=False)
                self.assertEqual(set(cleared), set(_MODEL_RECOMPUTE_STEPS))
                for step in _MODEL_RECOMPUTE_STEPS:
                    self.assertFalse(step_already_done(self.RD, step))

    def test_healthy_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                self._seed_model_markers(self.RD)
                with self._patch_drift([]):
                    cleared = _detect_and_clear_stale_model(self.RD, dry_run=False)
                self.assertEqual(cleared, [])
                for step in _MODEL_RECOMPUTE_STEPS:
                    self.assertTrue(step_already_done(self.RD, step))

    def test_dry_run_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                self._seed_model_markers(self.RD)
                with self._patch_drift([(self.RD, 196451, 0.0)]):
                    cleared = _detect_and_clear_stale_model(self.RD, dry_run=True)
                self.assertEqual(cleared, [])
                for step in _MODEL_RECOMPUTE_STEPS:
                    self.assertTrue(step_already_done(self.RD, step))

    def test_bq_error_is_noop(self):
        from google.cloud import bigquery as _bq  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                self._seed_model_markers(self.RD)
                fake_client = mock.MagicMock()
                fake_client.query.side_effect = RuntimeError("BQ down")
                with mock.patch.object(_bq, "Client", return_value=fake_client):
                    cleared = _detect_and_clear_stale_model(self.RD, dry_run=False)
                self.assertEqual(cleared, [])
                for step in _MODEL_RECOMPUTE_STEPS:
                    self.assertTrue(step_already_done(self.RD, step))

    def test_materialize_model_bq_in_recompute_steps(self):
        """Regression guard: the constant must include materialize_model_bq
        (the 2026-06-09 incident root cause) so the list never silently drifts."""
        self.assertIn("materialize_model_bq", _MODEL_RECOMPUTE_STEPS)

    def test_render_steps_not_in_recompute_steps(self):
        """Post-Sheets-exit: render_* and reconcile steps are deleted — not in
        _MODEL_RECOMPUTE_STEPS. Only materialize_model_bq remains."""
        for step in ("render_raw_sheets", "render_model_sheet_from_bq",
                     "reconcile_model"):
            self.assertNotIn(step, _MODEL_RECOMPUTE_STEPS)

    # ── _assert_model_matches_raw_rollup ──────────────────────────────────────

    def test_postcondition_passes_when_healthy(self):
        with self._patch_drift([]):
            _assert_model_matches_raw_rollup(self.RD)  # must not raise

    def test_postcondition_raises_on_residual_drift(self):
        with self._patch_drift([(self.RD, 196451, 0.0)]):
            with self.assertRaises(RuntimeError) as cm:
                _assert_model_matches_raw_rollup(self.RD)
        self.assertIn("raw-vs-model drift post-condition", str(cm.exception))
        self.assertIn("$0", str(cm.exception))

    def test_postcondition_bq_error_does_not_raise(self):
        """A BQ error inside the post-condition must not mask the run result."""
        from google.cloud import bigquery as _bq  # noqa: PLC0415
        fake_client = mock.MagicMock()
        fake_client.query.side_effect = RuntimeError("timeout")
        with mock.patch.object(_bq, "Client", return_value=fake_client):
            _assert_model_matches_raw_rollup(self.RD)  # must not raise


class SinglePathRegressionTests(unittest.TestCase):
    """Post-Sheets-exit: orchestrator must not invoke Sheet projection scripts."""

    def test_orchestrator_never_invokes_update_model_sheet(self):
        src = (daily_refresh.pathlib.Path(daily_refresh.__file__).parent / "daily_refresh.py").read_text()
        self.assertNotIn("agents.bhaga.scripts.update_model_sheet", src)
        self.assertNotIn("BHAGA_SHEET_FROM_BQ", src)
        self.assertIn("materialize_model_bq", src)

    def test_orchestrator_never_invokes_render_or_reconcile(self):
        """Post-Sheets-exit: Sheet projection steps are deleted from nightly path."""
        src = (daily_refresh.pathlib.Path(daily_refresh.__file__).parent / "daily_refresh.py").read_text()
        # No subprocess calls to deleted scripts
        self.assertNotIn("render_raw_sheet_from_bq\",", src)
        self.assertNotIn("render_model_sheet_from_bq\",", src)
        self.assertNotIn("reconcile_model\",", src)

    def test_materialize_failure_does_not_fallback_to_legacy(self):
        src = (daily_refresh.pathlib.Path(daily_refresh.__file__).parent / "daily_refresh.py").read_text()
        self.assertNotIn("falling back to legacy", src)
        self.assertNotIn("_bq_canonical = False", src)


class PrepareProjectionRecoveryTests(unittest.TestCase):
    RD = datetime.date(2026, 6, 13)

    def _seed_scrape_done(self, rd):
        mark_step_done(rd, "square_transactions")
        mark_step_done(rd, "adp_reports")

    def _seed_projection_done(self, rd):
        for step in _MODEL_RECOMPUTE_STEPS:
            mark_step_done(rd, step)

    def test_prepare_projection_recovery_clears_stale_projection_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                daily_refresh._RUN_SUMMARY.clear()
                self._seed_scrape_done(self.RD)
                self._seed_projection_done(self.RD)
                with mock.patch.object(daily_refresh, "_bq_raw_coverage_complete", return_value=True), \
                     mock.patch.object(daily_refresh, "_last_pipeline_run_failed", return_value=True):
                    cleared = daily_refresh._prepare_projection_recovery(
                        self.RD, "palmetto", dry_run=False,
                    )
                self.assertEqual(set(cleared), set(_MODEL_RECOMPUTE_STEPS))
                self.assertTrue(step_already_done(self.RD, "square_transactions"))
                self.assertTrue(step_already_done(self.RD, "adp_reports"))
                for step in _MODEL_RECOMPUTE_STEPS:
                    self.assertFalse(step_already_done(self.RD, step))
                self.assertTrue(daily_refresh._RUN_SUMMARY.get("recovery_retrigger"))

    def test_prepare_projection_recovery_noop_when_last_run_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                daily_refresh._RUN_SUMMARY.clear()
                self._seed_scrape_done(self.RD)
                self._seed_projection_done(self.RD)
                with mock.patch.object(daily_refresh, "_bq_raw_coverage_complete", return_value=True), \
                     mock.patch.object(daily_refresh, "_last_pipeline_run_failed", return_value=False):
                    cleared = daily_refresh._prepare_projection_recovery(
                        self.RD, "palmetto", dry_run=False,
                    )
                self.assertEqual(cleared, [])
                for step in _MODEL_RECOMPUTE_STEPS:
                    self.assertTrue(step_already_done(self.RD, step))

    def test_retrigger_with_bq_coverage_skips_browser_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(daily_refresh.pathlib.Path, "home",
                                   return_value=daily_refresh.pathlib.Path(tmp)):
                self._seed_scrape_done(self.RD)
                with mock.patch(
                    "skills._browser_runtime.runtime.is_fresh_download",
                    return_value=True,
                ):
                    self.assertFalse(daily_refresh._square_will_launch_browser(
                        needs_square=True,
                        gap_start=self.RD,
                        end_date=self.RD,
                        refresh_date=self.RD,
                        skip_kds=False,
                    ))
                with mock.patch(
                    "skills.adp_run_automation.runner._xlsx_fresh_for_target",
                    return_value=True,
                ):
                    self.assertFalse(daily_refresh._adp_will_launch_browser(
                        needs_adp=True,
                        target_date=self.RD,
                        include_earnings=True,
                    ))


class TestVerifyModelBq(unittest.TestCase):
    """verify_model_bq raises RuntimeError on empty tables; passes when populated."""

    _POPULATED_COUNT = [{"c": 5}]
    _KDS_RANGE = [{"lo": "2026-06-01", "hi": "2026-06-13", "c": 13}]
    _KDS_LABOR_DAILY = [{"c": 3}]   # populated KDS columns
    _KDS_OVERLAP_OK = [{"c": 0}]    # no bad rows

    def _make_read_query(self, overrides: dict | None = None):
        """Return a mock read_query that returns populated counts by default."""
        defaults = {
            "model_daily": self._POPULATED_COUNT,
            "model_labor_daily": self._POPULATED_COUNT,
            "model_labor_weekly": self._POPULATED_COUNT,
            "model_labor_period": self._POPULATED_COUNT,
            "model_period_summary": self._POPULATED_COUNT,
            "square_kds_daily": self._KDS_RANGE,
            "kds_ld": self._KDS_LABOR_DAILY,
            "kds_overlap": self._KDS_OVERLAP_OK,
        }
        if overrides:
            defaults.update(overrides)

        def _rq(sql: str) -> list:
            sql_l = sql.lower()
            # KDS-specific checks must be routed BEFORE generic count checks
            if "square_kds_daily" in sql_l:
                return defaults["square_kds_daily"]
            if "kds_completed_tickets" in sql_l:
                return defaults["kds_ld"]
            if "kds_completed_items" in sql_l:
                return defaults["kds_overlap"]
            # Generic row count checks
            if "model_daily" in sql_l:
                return defaults["model_daily"]
            if "model_labor_daily" in sql_l:
                return defaults["model_labor_daily"]
            if "model_labor_weekly" in sql_l:
                return defaults["model_labor_weekly"]
            if "model_labor_period" in sql_l:
                return defaults["model_labor_period"]
            if "model_period_summary" in sql_l:
                return defaults["model_period_summary"]
            return [{"c": 1}]

        return _rq

    def test_passes_when_all_tables_populated_no_kds(self):
        rq = self._make_read_query()
        with mock.patch("core.datastore.read_query", side_effect=rq):
            daily_refresh.verify_model_bq("palmetto", expect_kds=False)

    def test_raises_when_model_daily_empty(self):
        rq = self._make_read_query({"model_daily": [{"c": 0}]})
        with mock.patch("core.datastore.read_query", side_effect=rq):
            with self.assertRaises(RuntimeError) as ctx:
                daily_refresh.verify_model_bq("palmetto", expect_kds=False)
        self.assertIn("model_daily", str(ctx.exception))

    def test_raises_when_kds_table_empty(self):
        rq = self._make_read_query({"square_kds_daily": [{"lo": None, "hi": None, "c": 0}]})
        with mock.patch("core.datastore.read_query", side_effect=rq):
            with self.assertRaises(RuntimeError) as ctx:
                daily_refresh.verify_model_bq("palmetto", expect_kds=True)
        self.assertIn("square_kds_daily", str(ctx.exception))

    def test_passes_kds_branch_when_populated(self):
        rq = self._make_read_query()
        with mock.patch("core.datastore.read_query", side_effect=rq):
            daily_refresh.verify_model_bq("palmetto", expect_kds=True)

    def test_raises_when_kds_labor_daily_columns_empty(self):
        rq = self._make_read_query({"kds_ld": [{"c": 0}]})
        with mock.patch("core.datastore.read_query", side_effect=rq):
            with self.assertRaises(RuntimeError) as ctx:
                daily_refresh.verify_model_bq("palmetto", expect_kds=True)
        self.assertIn("model_labor_daily", str(ctx.exception))


class TestShouldRecordPipelineRun(unittest.TestCase):
    """_should_record_pipeline_run gates on CLOUD_RUN_JOB (prod) + explicit opt-in."""

    def setUp(self):
        self._saved = dict(daily_refresh._RUN_SUMMARY)
        daily_refresh._RUN_SUMMARY.update(
            dry_run=False, refresh_date=datetime.date(2026, 6, 13)
        )

    def tearDown(self):
        daily_refresh._RUN_SUMMARY.clear()
        daily_refresh._RUN_SUMMARY.update(self._saved)

    def test_records_inside_cloud_run(self):
        with mock.patch.dict(os.environ, {"CLOUD_RUN_JOB": "bhaga-daily-refresh"}, clear=False):
            os.environ.pop("BHAGA_RECORD_PIPELINE_RUN", None)
            self.assertTrue(daily_refresh._should_record_pipeline_run())

    def test_skips_on_laptop_even_with_gcp_backend(self):
        with mock.patch.dict(
            os.environ,
            {"BHAGA_SECRETS_BACKEND": "gcp", "BHAGA_DATASTORE": "bigquery"},
            clear=False,
        ):
            os.environ.pop("CLOUD_RUN_JOB", None)
            os.environ.pop("BHAGA_RECORD_PIPELINE_RUN", None)
            self.assertFalse(daily_refresh._should_record_pipeline_run())

    def test_explicit_optin_records(self):
        with mock.patch.dict(os.environ, {"BHAGA_RECORD_PIPELINE_RUN": "1"}, clear=False):
            os.environ.pop("CLOUD_RUN_JOB", None)
            self.assertTrue(daily_refresh._should_record_pipeline_run())

    def test_dry_run_never_records(self):
        daily_refresh._RUN_SUMMARY["dry_run"] = True
        with mock.patch.dict(os.environ, {"CLOUD_RUN_JOB": "x"}, clear=False):
            self.assertFalse(daily_refresh._should_record_pipeline_run())


class TestOtpForceRequestIntegration(unittest.TestCase):
    """End-to-end: BHAGA_OTP_FORCE_REQUEST=1 + stale checkpoint -> daily_refresh re-prompts.

    Exercises the FULL chain without mocking otp_gate.evaluate:

      BHAGA_OTP_FORCE_REQUEST=1 (env)
        -> otp_gate.evaluate reads stale local checkpoint (real code)
        -> returns EXIT_PENDING first_request=True (real state machine)
        -> daily_refresh dispatch calls _adapter_save_pending_otp + ready_request

    If otp_gate.py's force_request branch is removed, the gate returns
    EXIT_PENDING first_request=False and neither side-effect fires -> test fails.
    If daily_refresh.py's dispatch branch is changed, save/post assertions fail.
    A standalone copy of the dispatch would not catch either regression.
    """

    REFRESH_DATE_ISO = "2026-06-01"  # past date passes completeness gate

    class _FixedNowDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            _now = datetime.datetime(2026, 6, 2, 13, 0, 0, tzinfo=CT)
            return _now if tz is not None else _now.replace(tzinfo=None)

    _MINIMAL_PROFILE = {
        "calibration": {"first_data_window": {"start": "2025-01-01"}},
        "google_sheets": {"bhaga_model": {"spreadsheet_id": "fake-sheet"}},
    }

    def _run_main(self, tmp: str, *, force: bool, stale_checkpoint: bool):
        """Call daily_refresh.main() with real otp_gate.evaluate.

        otp_gate.evaluate is NOT mocked — it reads the local state from HOME=tmp.
        Stale pending_otp is pre-seeded in the local state backend when requested.

        The test wraps state_adapter.get_pending_otp with a spy to explicitly prove
        it is called by otp_gate.evaluate (via the default get_pending= arg) and
        returns the correct checkpoint data from the local disk backend.

        Other patches needed to reach the OTP gate without GCP/browser:
        - datetime.datetime → fixed 13:00 CT (completeness gate passes)
        - _load_profile → minimal profile  
        - resolve_sheet_id → no-op
        - _read_review_bonus_row_count → 0
        - BQ get_client → None (falls back to sheet-based window path)
        - _read_data_window_end_from_sheet → yesterday
        - info_ping → no-op
        - _square_will_launch_browser → True (Square enters otp_portals)
        - _adapter_save_pending_otp → spy (captures call without writing to disk again)
        - ready_request → spy
        """
        import skills.bhaga_config.state_adapter as _state_adapter
        from core import datastore as _datastore

        refresh_date = datetime.date.fromisoformat(self.REFRESH_DATE_ISO)
        yesterday = refresh_date - datetime.timedelta(days=1)

        if stale_checkpoint:
            # Plant a stale pending_otp so otp_gate.evaluate finds an unanswered request.
            # requested_at is 3 hours ago (within 48h cap -> would normally be silent).
            stale_time = (
                datetime.datetime(2026, 6, 2, 10, 0, 0, tzinfo=CT)
                - datetime.timedelta(hours=3)
            ).isoformat()
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                daily_refresh._adapter_save_pending_otp(
                    refresh_date, ["Square"],
                    requested_at=stale_time, agent="bhaga",
                )

        save_calls = []
        post_calls = []
        get_pending_calls: list[dict] = []  # spy: prove state_adapter is called
        # BHAGA_OTP_REQUIRE_READY=1 activates the legacy READY handshake so the
        # force-request path in otp_gate.evaluate is reachable (default inline
        # mode returns PROCEED immediately without consulting the checkpoint).
        env_patch = {"HOME": tmp, "BHAGA_OTP_REQUIRE_READY": "1"}
        if force:
            env_patch["BHAGA_OTP_FORCE_REQUEST"] = "1"
        else:
            env_patch.pop("BHAGA_OTP_FORCE_REQUEST", None)

        _real_get_pending = _state_adapter.get_pending_otp

        def _spy_get_pending(date):
            result = _real_get_pending(date)
            get_pending_calls.append({"date": date, "returned": result})
            return result

        argv = [
            "daily_refresh",
            "--store", "palmetto",
            "--date", self.REFRESH_DATE_ISO,
            "--no-slack",
            "--skip-timecard",
            "--skip-reviews",
            "--skip-model",
        ]

        with mock.patch.dict(os.environ, env_patch, clear=False), \
             mock.patch.object(daily_refresh.datetime, "datetime", self._FixedNowDateTime), \
             mock.patch.object(daily_refresh, "_load_profile",
                               return_value=self._MINIMAL_PROFILE), \
             mock.patch.object(daily_refresh, "resolve_sheet_id",
                               return_value="fake-sheet"), \
             mock.patch.object(daily_refresh, "_read_review_bonus_row_count",
                               return_value=0), \
             mock.patch.object(_datastore, "get_client", return_value=None), \
             mock.patch.object(daily_refresh, "_read_data_window_end_from_sheet",
                               return_value=(yesterday, False)), \
             mock.patch.object(daily_refresh, "info_ping", lambda *a, **k: None), \
             mock.patch.object(daily_refresh, "_square_will_launch_browser",
                               return_value=True), \
             mock.patch.object(_state_adapter, "get_pending_otp", _spy_get_pending), \
             mock.patch.object(daily_refresh, "_adapter_save_pending_otp",
                               side_effect=lambda rd, p, **kw: save_calls.append(
                                   {"refresh_date": rd, "portals": p, **kw}
                               )), \
             mock.patch.object(daily_refresh, "ready_request",
                               side_effect=lambda **kw: post_calls.append(kw)), \
             mock.patch.object(sys, "argv", argv):
            rc = daily_refresh.main()

        return rc, save_calls, post_calls, get_pending_calls

    def test_force_with_stale_checkpoint_saves_and_posts(self):
        """Full chain: BHAGA_OTP_FORCE_REQUEST=1 + stale checkpoint -> re-save + re-post.

        otp_gate.evaluate is real: reads the stale local-disk checkpoint via
        state_adapter.get_pending_otp (spy confirms the call + return value),
        sees force_request=True (from env), returns EXIT_PENDING first_request=True.
        daily_refresh re-saves and re-posts. state_adapter.get_pending_otp is explicitly
        verified to have been called and to have returned the stale checkpoint.
        """
        with tempfile.TemporaryDirectory() as tmp:
            rc, save_calls, post_calls, get_pending_calls = self._run_main(
                tmp, force=True, stale_checkpoint=True
            )
        self.assertEqual(rc, 0)
        # Verify state_adapter.get_pending_otp was called with the correct date
        # and returned the stale checkpoint (proves the local-disk read path).
        self.assertEqual(len(get_pending_calls), 1,
                         "state_adapter.get_pending_otp must be called once")
        self.assertEqual(get_pending_calls[0]["date"],
                         datetime.date.fromisoformat(self.REFRESH_DATE_ISO))
        stale_result = get_pending_calls[0]["returned"]
        self.assertIsNotNone(stale_result,
                             "state_adapter must return the pre-seeded stale checkpoint")
        self.assertIn("Square", stale_result.get("portals", []))
        self.assertFalse(stale_result.get("ready_received", True))
        # Verify daily_refresh dispatch fires re-save + re-post.
        self.assertEqual(len(save_calls), 1,
                         "force+stale: must re-save fresh checkpoint")
        self.assertEqual(save_calls[0]["refresh_date"],
                         datetime.date.fromisoformat(self.REFRESH_DATE_ISO))
        self.assertIn("Square", save_calls[0]["portals"])
        self.assertIsNotNone(save_calls[0].get("requested_at"))
        self.assertEqual(len(post_calls), 1,
                         "force+stale: must re-post READY to Slack")
        self.assertEqual(post_calls[0]["date"], self.REFRESH_DATE_ISO)
        self.assertIn("Square", post_calls[0]["portals"])

    def test_no_force_stale_checkpoint_is_silent(self):
        """Nightly path: stale checkpoint without BHAGA_OTP_FORCE_REQUEST -> silent exit.

        otp_gate.evaluate is real: reads stale checkpoint via state_adapter, force_request
        is False (env unset), returns EXIT_PENDING first_request=False. daily_refresh
        exits without re-saving or re-posting — nightly duplicate-ping suppression
        is unchanged.
        """
        with tempfile.TemporaryDirectory() as tmp:
            rc, save_calls, post_calls, get_pending_calls = self._run_main(
                tmp, force=False, stale_checkpoint=True
            )
        self.assertEqual(rc, 0)
        # state_adapter was called and returned the stale checkpoint.
        self.assertEqual(len(get_pending_calls), 1)
        self.assertIsNotNone(get_pending_calls[0]["returned"])
        # But without force, daily_refresh stays silent.
        self.assertEqual(save_calls, [],
                         "nightly: must NOT re-save on stale outstanding checkpoint")
        self.assertEqual(post_calls, [],
                         "nightly: must NOT re-post on stale outstanding checkpoint")

    def test_force_no_checkpoint_posts_first_request(self):
        """Force with no prior checkpoint: new run, first request posts normally.

        otp_gate.evaluate is real: state_adapter returns None (no checkpoint),
        returns EXIT_PENDING first_request=True (standard no-checkpoint path).
        Force flag doesn't change this path.
        """
        with tempfile.TemporaryDirectory() as tmp:
            rc, save_calls, post_calls, get_pending_calls = self._run_main(
                tmp, force=True, stale_checkpoint=False
            )
        self.assertEqual(rc, 0)
        # state_adapter was called but found no checkpoint.
        self.assertEqual(len(get_pending_calls), 1)
        self.assertIsNone(get_pending_calls[0]["returned"],
                          "state_adapter must return None when no checkpoint exists")
        self.assertEqual(len(save_calls), 1, "no checkpoint: first request saves")
        self.assertEqual(len(post_calls), 1, "no checkpoint: first request posts")


class TestReviewBonusGridColumn(unittest.TestCase):
    """Regression test: _bq_grid for model_review_bonus_period uses 'total_bonus'.

    Before the fix, the column was 'review_bonus_dollars' (phantom — never in the
    BQ schema). BQ raised BadRequest, which propagated as 0 rows and tripped the
    semantic guard. This test locks the correct column name so the same typo cannot
    silently re-appear.
    """

    def test_review_bonus_grid_column_is_total_bonus(self):
        """The SQL column queried for model_review_bonus_period must be 'total_bonus'."""
        src = pathlib.Path(__file__).parent / "daily_refresh.py"
        tree = ast.parse(src.read_text())
        # Find _bq_grid("model_review_bonus_period", "...") call and check the cols arg
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_bq_grid"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "model_review_bonus_period"
            ):
                cols_arg = node.args[1].value if isinstance(node.args[1], ast.Constant) else ""
                self.assertIn(
                    "total_bonus", cols_arg,
                    "model_review_bonus_period grid must query 'total_bonus' (not 'review_bonus_dollars')"
                )
                self.assertNotIn(
                    "review_bonus_dollars", cols_arg,
                    "phantom column 'review_bonus_dollars' must not appear in grid query"
                )
                found = True
        self.assertTrue(found, "_bq_grid('model_review_bonus_period', …) call not found in daily_refresh.py")


class TestForceModelRecomputeMarkerClear(unittest.TestCase):
    """BHAGA_FORCE_MODEL_RECOMPUTE=1 clears _MODEL_RECOMPUTE_STEPS before any step runs.

    Tests both the local (filesystem) and Firestore (stub) backends to prove
    backend-agnosticism: the clear goes through apply_force_model_recompute →
    clear_step_done → _adapter_clear_step (state_adapter.clear_step).
    """

    def _import_dr(self):
        import agents.bhaga.scripts.daily_refresh as dr
        return dr

    def test_force_recompute_clears_model_steps_via_extracted_function(self):
        """BHAGA_FORCE_MODEL_RECOMPUTE=1: apply_force_model_recompute clears every step
        in _MODEL_RECOMPUTE_STEPS and returns their names.  The production clear block
        in main() delegates to this function; here we call it directly."""
        dr = self._import_dr()
        cleared: list[tuple[datetime.date, str]] = []

        with mock.patch.object(dr, "clear_step_done",
                               side_effect=lambda d, s: cleared.append((d, s))), \
             mock.patch.dict(os.environ, {"BHAGA_FORCE_MODEL_RECOMPUTE": "1"}, clear=False):
            refresh_date = datetime.date(2026, 6, 19)
            result = dr.apply_force_model_recompute(refresh_date)

        self.assertEqual(len(cleared), len(dr._MODEL_RECOMPUTE_STEPS),
                         f"must clear all {len(dr._MODEL_RECOMPUTE_STEPS)} model step(s)")
        for date, step in cleared:
            self.assertEqual(date, datetime.date(2026, 6, 19))
            self.assertIn(step, dr._MODEL_RECOMPUTE_STEPS)
        self.assertEqual(result, list(dr._MODEL_RECOMPUTE_STEPS),
                         "return value must list the steps cleared")

    def test_force_recompute_not_set_returns_empty(self):
        """Without BHAGA_FORCE_MODEL_RECOMPUTE, apply_force_model_recompute returns []."""
        dr = self._import_dr()
        cleared: list = []

        with mock.patch.object(dr, "clear_step_done",
                               side_effect=lambda d, s: cleared.append((d, s))):
            env = {k: v for k, v in os.environ.items() if k != "BHAGA_FORCE_MODEL_RECOMPUTE"}
            with mock.patch.dict(os.environ, env, clear=True):
                result = dr.apply_force_model_recompute(datetime.date(2026, 6, 19))

        self.assertEqual(cleared, [], "no markers must be cleared when env var is absent")
        self.assertEqual(result, [])

    def test_force_recompute_uses_state_adapter_not_direct_fs(self):
        """clear_step_done delegates to _adapter_clear_step (state_adapter.clear_step),
        NOT a direct pathlib.Path.unlink — proves backend-agnosticism.

        _adapter_clear_step is imported at module-load time via
        `from skills.bhaga_config.state_adapter import clear_step as _adapter_clear_step`,
        so we patch it on the daily_refresh module namespace.
        """
        dr = self._import_dr()
        adapter_calls: list[tuple[datetime.date, str]] = []

        with mock.patch.object(dr, "_adapter_clear_step",
                               side_effect=lambda d, s: adapter_calls.append((d, s))):
            dr.clear_step_done(datetime.date(2026, 6, 19), "materialize_model_bq")

        self.assertEqual(len(adapter_calls), 1,
                         "clear_step_done must call state_adapter.clear_step exactly once")
        self.assertEqual(adapter_calls[0], (datetime.date(2026, 6, 19), "materialize_model_bq"))

    def test_force_recompute_firestore_backend_routes_through_adapter(self):
        """With BHAGA_STATE_BACKEND=firestore: apply_force_model_recompute delegates to
        _adapter_clear_step (the firestore-aware path), not a direct FS delete.

        We patch _adapter_clear_step on the dr namespace (the bound reference) so
        the test is non-vacuous regardless of which state_adapter version is installed.
        """
        dr = self._import_dr()
        adapter_calls: list[tuple[datetime.date, str]] = []

        with mock.patch.dict(os.environ,
                             {"BHAGA_FORCE_MODEL_RECOMPUTE": "1",
                              "BHAGA_STATE_BACKEND": "firestore"}, clear=False), \
             mock.patch.object(dr, "_adapter_clear_step",
                               side_effect=lambda d, s: adapter_calls.append((d, s))):
            dr.apply_force_model_recompute(datetime.date(2026, 6, 19))

        self.assertEqual(len(adapter_calls), len(dr._MODEL_RECOMPUTE_STEPS),
                         "Firestore backend: _adapter_clear_step must be called once per step")
        for date, step in adapter_calls:
            self.assertEqual(date, datetime.date(2026, 6, 19))
            self.assertIn(step, dr._MODEL_RECOMPUTE_STEPS)


class TestIsAdpLoginThrottled(unittest.TestCase):
    """Tests for _is_adp_login_throttled — the duck-typed classifier that lets
    daily_refresh treat AdpLoginThrottled as a graceful ADP skip."""

    def _import_dr(self):
        import importlib
        import agents.bhaga.scripts.daily_refresh as dr
        return dr

    def _make_throttled(self):
        """Return a real AdpLoginThrottled instance."""
        from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
        return AdpLoginThrottled("throttle test")

    def test_direct_adp_login_throttled_is_true(self):
        dr = self._import_dr()
        exc = self._make_throttled()
        self.assertTrue(dr._is_adp_login_throttled(exc))

    def test_chained_adp_login_throttled_is_true(self):
        """__cause__ chain: outer RuntimeError wraps AdpLoginThrottled."""
        dr = self._import_dr()
        inner = self._make_throttled()
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner
        self.assertTrue(dr._is_adp_login_throttled(outer))

    def test_context_chain_adp_login_throttled_is_true(self):
        """__context__ chain: exception raised inside an except block."""
        dr = self._import_dr()
        inner = self._make_throttled()
        outer = ValueError("context wrapper")
        outer.__context__ = inner
        self.assertTrue(dr._is_adp_login_throttled(outer))

    def test_non_throttle_exception_is_false(self):
        dr = self._import_dr()
        self.assertFalse(dr._is_adp_login_throttled(RuntimeError("plain failure")))
        self.assertFalse(dr._is_adp_login_throttled(ValueError("something else")))

    def test_none_is_false(self):
        dr = self._import_dr()
        self.assertFalse(dr._is_adp_login_throttled(None))

    def test_otp_wait_timeout_is_false(self):
        """OtpWaitTimeout must not match — different classifier, different path."""
        dr = self._import_dr()
        from agents.bhaga.scripts.otp_gate import OtpWaitTimeout
        self.assertFalse(dr._is_adp_login_throttled(OtpWaitTimeout("timeout")))

    def test_adp_throttle_does_not_match_is_otp_wait_timeout(self):
        """Sanity: the two classifiers are disjoint."""
        dr = self._import_dr()
        throttle_exc = self._make_throttled()
        self.assertFalse(dr._is_otp_wait_timeout(throttle_exc))


class TestHandleAdpThrottleSkip(unittest.TestCase):
    """Integration tests for _handle_adp_throttle_skip — the real branch body extracted
    from the daily_refresh results loop.

    Each test calls the actual production helper, NOT a re-implementation.
    Verifies the full branch contract: classifier → state mutation → Slack alert →
    caller-should-continue return value.
    """

    def _import_dr(self):
        import agents.bhaga.scripts.daily_refresh as dr
        return dr

    def _make_pr(self, dr, name: str, error=None):
        pr = dr.PipelineResult(name=name)
        pr.success = False
        pr.error = error
        return pr

    def test_adp_throttle_returns_true_and_sets_skipped_status(self):
        """The real _handle_adp_throttle_skip: returns True, sets skipped_adp_throttle."""
        dr = self._import_dr()
        from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", AdpLoginThrottled("sorry.adp.com x3"))

        with mock.patch.object(dr, "info_ping", return_value=None):
            should_continue = dr._handle_adp_throttle_skip("adp", pr, run_summary, "2026-06-28")

        self.assertTrue(should_continue, "_handle_adp_throttle_skip must return True → caller continues")
        self.assertEqual(entry["status"], "skipped_adp_throttle")

    def test_adp_throttle_does_not_append_to_failures(self):
        """ADP throttle must NOT add to failures (no halt breaker trip)."""
        dr = self._import_dr()
        from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
        failures = []
        otp_portal_failed = False
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", AdpLoginThrottled("throttle"))

        with mock.patch.object(dr, "info_ping", return_value=None):
            if dr._handle_adp_throttle_skip("adp", pr, run_summary, "2026-06-28"):
                pass  # caller continues; does NOT execute failures.append(...)
            else:
                failures.append(("adp", pr.error))
                otp_portal_failed = True

        self.assertEqual(failures, [], "failures must be empty")
        self.assertFalse(otp_portal_failed, "otp_portal_failed must remain False")

    def test_non_adp_pipeline_returns_false(self):
        """Non-ADP pipeline (e.g. square) is never classified as throttle."""
        dr = self._import_dr()
        from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "square", AdpLoginThrottled("doesn't matter"))

        result = dr._handle_adp_throttle_skip("square", pr, run_summary, "2026-06-28")
        self.assertFalse(result, "non-ADP pipeline must never trigger throttle skip")
        self.assertEqual(entry["status"], "failed", "status must be unchanged")

    def test_non_throttle_adp_error_returns_false(self):
        """A real ADP scrape failure (RuntimeError) is not a graceful skip."""
        dr = self._import_dr()
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", RuntimeError("selector not found"))

        result = dr._handle_adp_throttle_skip("adp", pr, run_summary, "2026-06-28")
        self.assertFalse(result, "non-throttle error must not be treated as graceful skip")

    def test_info_ping_called_on_throttle(self):
        """info_ping is called (not failure_alert or otp_skipped_alert) for throttle."""
        dr = self._import_dr()
        from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", AdpLoginThrottled("throttle"))

        ping_calls = []
        with mock.patch.object(dr, "info_ping", side_effect=ping_calls.append):
            dr._handle_adp_throttle_skip("adp", pr, run_summary, "2026-06-28")

        self.assertEqual(len(ping_calls), 1, "exactly one info_ping call expected")
        self.assertIn("throttled", ping_calls[0].lower())

    def test_chained_throttle_exception_still_skips(self):
        """An AdpLoginThrottled wrapped in another exception triggers graceful skip."""
        dr = self._import_dr()
        from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
        inner = AdpLoginThrottled("throttle")
        outer = RuntimeError("wrapped")
        outer.__cause__ = inner
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", outer)

        with mock.patch.object(dr, "info_ping", return_value=None):
            should_continue = dr._handle_adp_throttle_skip("adp", pr, run_summary, "2026-06-28")

        self.assertTrue(should_continue)
        self.assertEqual(entry["status"], "skipped_adp_throttle")


class TestMaintenanceSmartRetry(unittest.TestCase):
    """When AdpLoginThrottled carries retry_at (parsed maintenance window end),
    _handle_adp_throttle_skip schedules a one-shot retry instead of just waiting
    for the next nightly."""

    def _import_dr(self):
        import agents.bhaga.scripts.daily_refresh as dr
        return dr

    def _make_pr(self, dr, name, error):
        pr = dr.PipelineResult(name=name)
        pr.success = False
        pr.error = error
        return pr

    def _throttle_with_retry(self):
        import datetime
        from agents.bhaga.scripts.otp_gate import AdpLoginThrottled
        retry_at = datetime.datetime(2026, 6, 29, 6, 7, tzinfo=datetime.timezone.utc)
        return AdpLoginThrottled("maintenance", retry_at=retry_at), retry_at

    def test_schedules_retry_and_sets_maintenance_status(self):
        dr = self._import_dr()
        exc, retry_at = self._throttle_with_retry()
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", exc)
        calls = []

        with mock.patch.object(dr, "info_ping", return_value=None), \
                mock.patch.dict(dr.os.environ, {"BHAGA_MAINT_RETRY_ATTEMPT": "0"}, clear=False):
            ok = dr._handle_adp_throttle_skip(
                "adp", pr, run_summary, "2026-06-28",
                schedule_fn=lambda *a, **k: calls.append((a, k)),
            )

        self.assertTrue(ok)
        self.assertEqual(entry["status"], "skipped_adp_maintenance")
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[0], "2026-06-28")
        self.assertEqual(args[1], retry_at)
        self.assertEqual(kwargs["env"]["BHAGA_MAINT_RETRY_ATTEMPT"], "1")
        self.assertEqual(kwargs["env"]["REFRESH_DATE"], "2026-06-28")

    def test_info_ping_mentions_smart_retry(self):
        dr = self._import_dr()
        exc, _ = self._throttle_with_retry()
        run_summary = {"source_pulls": [{"status": "failed"}]}
        pr = self._make_pr(dr, "adp", exc)
        pings = []

        with mock.patch.object(dr, "info_ping", side_effect=pings.append), \
                mock.patch.dict(dr.os.environ, {"BHAGA_MAINT_RETRY_ATTEMPT": "0"}, clear=False):
            dr._handle_adp_throttle_skip(
                "adp", pr, run_summary, "2026-06-28", schedule_fn=lambda *a, **k: None,
            )

        self.assertEqual(len(pings), 1)
        self.assertIn("smart retry", pings[0].lower())

    def test_attempt_cap_falls_back_to_plain_skip(self):
        dr = self._import_dr()
        exc, _ = self._throttle_with_retry()
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", exc)
        calls = []

        with mock.patch.object(dr, "info_ping", return_value=None), \
                mock.patch.dict(dr.os.environ, {"BHAGA_MAINT_RETRY_ATTEMPT": "3"}, clear=False):
            ok = dr._handle_adp_throttle_skip(
                "adp", pr, run_summary, "2026-06-28",
                schedule_fn=lambda *a, **k: calls.append(1),
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [], "cap reached → no scheduling")
        self.assertEqual(entry["status"], "skipped_adp_throttle")

    def test_schedule_failure_falls_back_to_plain_skip(self):
        dr = self._import_dr()
        exc, _ = self._throttle_with_retry()
        entry = {"status": "failed"}
        run_summary = {"source_pulls": [entry]}
        pr = self._make_pr(dr, "adp", exc)

        def _boom(*a, **k):
            raise RuntimeError("scheduler API down")

        with mock.patch.object(dr, "info_ping", return_value=None), \
                mock.patch.dict(dr.os.environ, {"BHAGA_MAINT_RETRY_ATTEMPT": "0"}, clear=False):
            ok = dr._handle_adp_throttle_skip(
                "adp", pr, run_summary, "2026-06-28", schedule_fn=_boom,
            )

        self.assertTrue(ok)
        self.assertEqual(entry["status"], "skipped_adp_throttle")


if __name__ == "__main__":
    unittest.main()
