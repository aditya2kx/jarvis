#!/usr/bin/env python3
"""Unit tests for agents.bhaga.scripts.daily_refresh_wrapper.

Run:
    python3 -m unittest agents.bhaga.scripts.test_daily_refresh_wrapper -v

Covers the wrapper's day-marker keying + gate behavior, in particular the
overflow-failure regression that motivated the fix:

  - Last night's cron (refresh_date=2026-05-22) takes ~3h, returns past
    midnight CT. `_now_ct().date()` evaluates to 2026-05-23 at write time.
  - Pre-fix: marker was keyed by `_now_ct().date()` -> wrote
    "2026-05-23 status: failed" -> tonight's 21:00 CT cron sees a
    matching marker for today and silently gates off. Next-night cron
    is silently blocked by the prior night's overflow failure.
  - Post-fix: marker is keyed by `refresh_date` (the date the gate
    decided to run for) -> writes "2026-05-22 status: failed" -> tonight's
    cron sees no matching marker for today -> fires.

Companion to commit 86e315a (per-step .done markers keyed by refresh_date).
"""

from __future__ import annotations

import datetime
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import daily_refresh_wrapper as wrapper
from agents.bhaga.scripts.daily_refresh_wrapper import CT, gate, write_marker


class _MarkerSandbox:
    """Context manager that redirects STATE_DIR / MARKER_FILE / WRAPPER_LOG /
    REFRESH_LOG into a fresh tempdir so tests don't touch ~/.bhaga/state."""

    def __init__(self) -> None:
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._patches: list = []

    def __enter__(self) -> pathlib.Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        state_dir = root / "state"
        state_dir.mkdir(parents=True)
        self._patches = [
            mock.patch.object(wrapper, "STATE_DIR", state_dir),
            mock.patch.object(wrapper, "MARKER_FILE", state_dir / "last_run_ct_date.txt"),
            mock.patch.object(wrapper, "WRAPPER_LOG", state_dir / "wrapper.log"),
            mock.patch.object(wrapper, "REFRESH_LOG", state_dir / "refresh.log"),
        ]
        for p in self._patches:
            p.start()
        return state_dir

    def __exit__(self, *exc) -> None:
        for p in self._patches:
            p.stop()
        if self._tmp is not None:
            self._tmp.cleanup()


class WriteMarkerKeyingTests(unittest.TestCase):
    """write_marker MUST key by refresh_date, not _now_ct().date().

    The overflow scenario (run started 21:00 CT 5/22, finished 00:11 CT 5/23):
    if we key by _now_ct() at write time, the marker lands on 5/23 and the
    next night's cron sees a same-day marker and silently skips itself.
    """

    def test_success_marker_keyed_by_refresh_date_not_today(self):
        with _MarkerSandbox():
            refresh_date = datetime.date(2026, 5, 22)
            write_marker(refresh_date, status="success", rc=0)
            body = wrapper.MARKER_FILE.read_text()
            first_line = body.split("\n", 1)[0]
            self.assertEqual(
                first_line, "2026-05-22",
                f"marker first line should be the refresh_date 2026-05-22, "
                f"got {first_line!r}; full body:\n{body}",
            )
            self.assertIn("status: success", body)
            self.assertIn("rc: 0", body)

    def test_failure_marker_keyed_by_refresh_date(self):
        with _MarkerSandbox():
            refresh_date = datetime.date(2026, 5, 22)
            write_marker(refresh_date, status="failed", rc=1)
            body = wrapper.MARKER_FILE.read_text()
            first_line = body.split("\n", 1)[0]
            self.assertEqual(first_line, "2026-05-22")
            self.assertIn("status: failed", body)
            self.assertIn("rc: 1", body)
            # Failure markers also embed the rerun hint.
            self.assertIn("rerun:", body)

    def test_success_and_failure_use_same_key_format(self):
        # Sanity: both branches produce a parseable first line that the
        # gate's `first_line == today_ct.isoformat()` check can compare
        # against.
        for status in ("success", "failed"):
            with _MarkerSandbox():
                refresh_date = datetime.date(2026, 5, 22)
                write_marker(refresh_date, status=status, rc=(0 if status == "success" else 1))
                first_line = wrapper.MARKER_FILE.read_text().split("\n", 1)[0]
                # Round-trip through fromisoformat — guarantees the gate
                # comparison can never silently coerce.
                self.assertEqual(
                    datetime.date.fromisoformat(first_line),
                    refresh_date,
                )


class GateOverflowBoundaryTests(unittest.TestCase):
    """Behavioral tests for the gate across the midnight-CT boundary.

    `_now_ct` is monkeypatched to return a fixed CT datetime so the tests
    are stable regardless of wall-clock time.
    """

    @staticmethod
    def _ct(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime.datetime:
        return datetime.datetime(year, month, day, hour, minute, tzinfo=CT)

    def test_gate_lets_next_night_through_after_overflow_failure(self):
        # Yesterday's cron crossed midnight: marker keyed by refresh_date
        # 2026-05-22 status: failed. Tonight's 21:00 CT cron must fire.
        with _MarkerSandbox():
            wrapper.MARKER_FILE.write_text(
                "2026-05-22\n"
                "status: failed\n"
                "attempted_at: 2026-05-23T05:11:50.929079+00:00\n"
                "rc: 1\n"
                "rerun: python3 .../daily_refresh_wrapper.py --force\n"
            )
            with mock.patch.object(
                wrapper, "_now_ct",
                return_value=self._ct(2026, 5, 23, 21, 0),
            ):
                should_run, reason, refresh_date = gate(
                    force=False, simulate_ct=None,
                )
            self.assertTrue(
                should_run,
                f"gate must fire when prior marker is for yesterday; reason={reason}",
            )
            self.assertEqual(refresh_date, datetime.date(2026, 5, 23))

    def test_gate_blocks_same_night_replay_after_failure(self):
        # Tonight's cron already ran and failed; marker keyed for today.
        # A 21:15 re-wake must gate off (strict 1-attempt).
        with _MarkerSandbox():
            wrapper.MARKER_FILE.write_text(
                "2026-05-23\n"
                "status: failed\n"
                "attempted_at: 2026-05-24T02:00:00+00:00\n"
                "rc: 1\n"
            )
            with mock.patch.object(
                wrapper, "_now_ct",
                return_value=self._ct(2026, 5, 23, 21, 15),
            ):
                should_run, reason, refresh_date = gate(
                    force=False, simulate_ct=None,
                )
            self.assertFalse(
                should_run,
                f"gate must skip same-night replay after failure; reason={reason}",
            )
            self.assertEqual(refresh_date, datetime.date(2026, 5, 23))

    def test_gate_blocks_same_night_after_success(self):
        with _MarkerSandbox():
            wrapper.MARKER_FILE.write_text(
                "2026-05-23\n"
                "status: success\n"
                "attempted_at: 2026-05-24T02:00:00+00:00\n"
                "rc: 0\n"
            )
            with mock.patch.object(
                wrapper, "_now_ct",
                return_value=self._ct(2026, 5, 23, 22, 0),
            ):
                should_run, reason, refresh_date = gate(
                    force=False, simulate_ct=None,
                )
            self.assertFalse(should_run, f"reason={reason}")
            self.assertEqual(refresh_date, datetime.date(2026, 5, 23))

    def test_gate_no_marker_means_fire_at_target_hour(self):
        with _MarkerSandbox():
            # No marker file at all.
            self.assertFalse(wrapper.MARKER_FILE.exists())
            with mock.patch.object(
                wrapper, "_now_ct",
                return_value=self._ct(2026, 5, 23, 21, 0),
            ):
                should_run, reason, refresh_date = gate(
                    force=False, simulate_ct=None,
                )
            self.assertTrue(should_run, f"reason={reason}")
            self.assertEqual(refresh_date, datetime.date(2026, 5, 23))

    def test_gate_off_hour_skips_even_without_marker(self):
        with _MarkerSandbox():
            self.assertFalse(wrapper.MARKER_FILE.exists())
            with mock.patch.object(
                wrapper, "_now_ct",
                return_value=self._ct(2026, 5, 23, 13, 0),
            ):
                should_run, reason, _ = gate(force=False, simulate_ct=None)
            self.assertFalse(should_run, f"reason={reason}")
            self.assertIn("13", reason)

    def test_force_overrides_marker(self):
        # --force always fires regardless of marker or hour.
        with _MarkerSandbox():
            wrapper.MARKER_FILE.write_text(
                "2026-05-23\nstatus: success\nattempted_at: ...\nrc: 0\n"
            )
            with mock.patch.object(
                wrapper, "_now_ct",
                return_value=self._ct(2026, 5, 23, 21, 0),
            ):
                should_run, reason, refresh_date = gate(force=True, simulate_ct=None)
            self.assertTrue(should_run)
            self.assertIn("--force", reason)
            self.assertEqual(refresh_date, datetime.date(2026, 5, 23))


if __name__ == "__main__":
    unittest.main(verbosity=2)
