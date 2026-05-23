#!/usr/bin/env python3
"""Regression tests for the 2026-05-23 silent-partial-success class.

That morning's run reported exit 0 and wrote every per-step `.done`
marker, but `bhaga_model > config.data_window_end` did not advance and
189 freshly-merged Square rows never made it to the raw sheets. Root
cause was `skills/square_tips/transactions_backend._to_iana` silently
dropping rows whose Time Zone column was `Asia/Calcutta` (operator
traveling in India). That bug is fixed in
`skills/square_tips/test_transactions_backend.py`.

This file owns the two ORCHESTRATOR-LEVEL guards added at the same
time so that the SAME class of bug cannot pass silently again, no
matter which downstream parser swallows a row in the future:

    1. `_assert_master_not_older_than_gap` — pre-flight in
       write_raw_sheets. master.csv mtime must be >= gap_csv mtime.
       Catches the case where consolidate_csv silently failed to
       rewrite the master.

    2. `_assert_data_advanced_post_condition` — final guard before
       success heartbeat. Re-reads data_window_end and refuses to
       declare success if rows_added_from_gap > 0 but the window did
       not advance.

Both are pure functions, so the tests stand up zero infrastructure.

Run:
    python3 -m unittest agents.bhaga.scripts.test_silent_partial_success -v
"""

from __future__ import annotations

import datetime
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts.daily_refresh import (
    _assert_data_advanced_post_condition,
    _assert_master_not_older_than_gap,
)


class AssertMasterNotOlderThanGapTests(unittest.TestCase):
    """`_assert_master_not_older_than_gap` pre-flight check."""

    def _write(self, path: pathlib.Path, mtime: float) -> pathlib.Path:
        path.write_text("Transaction ID,Date\n")
        os.utime(path, (mtime, mtime))
        return path

    def test_no_gap_csv_is_a_noop(self) -> None:
        # --skip-square / no-fresh-gap case: nothing to compare.
        with tempfile.TemporaryDirectory() as td:
            master = pathlib.Path(td) / "transactions-master.csv"
            self._write(master, time.time())
            _assert_master_not_older_than_gap(master_csv=master, gap_csv=None)

    def test_gap_exists_but_master_missing_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            gap = self._write(pathlib.Path(td) / "transactions-2026-05-22.csv", time.time())
            master = pathlib.Path(td) / "transactions-master.csv"
            with self.assertRaises(RuntimeError) as cm:
                _assert_master_not_older_than_gap(master_csv=master, gap_csv=gap)
            self.assertIn("master CSV does not", str(cm.exception))

    def test_master_older_than_gap_raises(self) -> None:
        # master.csv was last touched BEFORE the gap CSV was downloaded —
        # consolidate_csv must have silently failed to rewrite master.
        with tempfile.TemporaryDirectory() as td:
            t = time.time()
            master = self._write(pathlib.Path(td) / "transactions-master.csv", t - 100)
            gap = self._write(pathlib.Path(td) / "transactions-gap.csv", t)
            with self.assertRaises(RuntimeError) as cm:
                _assert_master_not_older_than_gap(master_csv=master, gap_csv=gap)
            msg = str(cm.exception)
            self.assertIn("OLDER than the gap", msg)

    def test_master_newer_than_gap_passes(self) -> None:
        # Normal post-consolidate state: master was just rewritten so its
        # mtime is at or after the gap CSV's.
        with tempfile.TemporaryDirectory() as td:
            t = time.time()
            gap = self._write(pathlib.Path(td) / "transactions-gap.csv", t - 10)
            master = self._write(pathlib.Path(td) / "transactions-master.csv", t)
            _assert_master_not_older_than_gap(master_csv=master, gap_csv=gap)

    def test_equal_mtimes_pass(self) -> None:
        # Edge case: filesystem resolution may collapse a fast consolidate
        # into the same mtime as the gap. That MUST still pass.
        with tempfile.TemporaryDirectory() as td:
            t = time.time()
            gap = self._write(pathlib.Path(td) / "transactions-gap.csv", t)
            master = self._write(pathlib.Path(td) / "transactions-master.csv", t)
            _assert_master_not_older_than_gap(master_csv=master, gap_csv=gap)


class AssertDataAdvancedPostConditionTests(unittest.TestCase):
    """`_assert_data_advanced_post_condition` final guard."""

    PREV = datetime.date(2026, 5, 20)
    REFRESH = datetime.date(2026, 5, 22)

    def test_fires_when_rows_added_but_window_did_not_advance(self) -> None:
        # The exact 2026-05-23 incident.
        with self.assertRaises(RuntimeError) as cm:
            _assert_data_advanced_post_condition(
                prev_end=self.PREV,
                post_end=self.PREV,  # did not advance
                rows_added_from_gap=189,
                update_model_ran=True,
                refresh_date=self.REFRESH,
            )
        msg = str(cm.exception)
        self.assertIn("did NOT advance", msg)
        self.assertIn("189", msg)
        self.assertIn(self.PREV.isoformat(), msg)
        self.assertIn(self.REFRESH.isoformat(), msg)

    def test_silent_when_no_gap_csv_in_this_run(self) -> None:
        # rows_added_from_gap == 0 → nothing was supposed to advance.
        # MUST be silent so an idempotent recovery doesn't false-positive.
        _assert_data_advanced_post_condition(
            prev_end=self.PREV,
            post_end=self.PREV,  # didn't move — that's fine
            rows_added_from_gap=0,
            update_model_ran=True,
            refresh_date=self.REFRESH,
        )

    def test_silent_when_window_advanced(self) -> None:
        # The healthy path: 189 rows merged and data_window_end moved forward.
        _assert_data_advanced_post_condition(
            prev_end=self.PREV,
            post_end=self.REFRESH,  # advanced to refresh_date
            rows_added_from_gap=189,
            update_model_ran=True,
            refresh_date=self.REFRESH,
        )

    def test_silent_on_fresh_install(self) -> None:
        # prev_end is None on a fresh install — comparison is meaningless,
        # so the guard must not fire.
        _assert_data_advanced_post_condition(
            prev_end=None,
            post_end=self.REFRESH,
            rows_added_from_gap=1000,
            update_model_ran=True,
            refresh_date=self.REFRESH,
        )

    def test_silent_when_update_model_did_not_run(self) -> None:
        # Operator passed --skip-model, or update_model_sheet failed and
        # was already reported. The guard is a last line of defense for
        # the SUCCESS path only — if a step already failed, don't double
        # down.
        _assert_data_advanced_post_condition(
            prev_end=self.PREV,
            post_end=self.PREV,
            rows_added_from_gap=189,
            update_model_ran=False,
            refresh_date=self.REFRESH,
        )

    def test_raises_when_post_end_unreadable_after_advance_expected(self) -> None:
        # Defensive: rows were merged AND model ran, but the post-run
        # data_window_end read failed. We can't prove the data made it
        # through — so the guard refuses to declare success.
        with self.assertRaises(RuntimeError) as cm:
            _assert_data_advanced_post_condition(
                prev_end=self.PREV,
                post_end=None,
                rows_added_from_gap=189,
                update_model_ran=True,
                refresh_date=self.REFRESH,
            )
        self.assertIn("could not be re-read", str(cm.exception))

    def test_post_end_strictly_greater(self) -> None:
        # An "advance" must be strictly greater than prev_end. Equal is
        # the failure case (the 2026-05-23 shape).
        with self.assertRaises(RuntimeError):
            _assert_data_advanced_post_condition(
                prev_end=self.PREV,
                post_end=self.PREV,
                rows_added_from_gap=1,
                update_model_ran=True,
                refresh_date=self.REFRESH,
            )

    def test_post_end_less_than_prev_also_fails(self) -> None:
        # Should never happen but defend against weird Sheets writes
        # rolling the window BACKWARDS while still claiming success.
        with self.assertRaises(RuntimeError):
            _assert_data_advanced_post_condition(
                prev_end=self.PREV,
                post_end=self.PREV - datetime.timedelta(days=1),
                rows_added_from_gap=189,
                update_model_ran=True,
                refresh_date=self.REFRESH,
            )


if __name__ == "__main__":
    unittest.main()
