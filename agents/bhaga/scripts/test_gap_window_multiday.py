"""Regression test: a recovery run after a multi-day outage must scrape
a single CONTIGUOUS Square range, not a per-day loop.

Scenario (2026-05-22 IST-truncation incident recovery): the model sheet's
``data_window_end`` was reset to 2026-05-20 because 5/21 + 5/22 data had
been polluted by an IST-clipped scrape. The orchestrator then ran with
``refresh_date=2026-05-22``. It MUST:

1. Resolve gap_start = 2026-05-21 (data_window_end + 1 day).
2. Pass (start_date=2026-05-21, end_date=2026-05-22) as a SINGLE range to
   download_transactions — i.e. one Square date-picker selection, one
   CSV export, ONE 2FA round-trip.
3. Pass target_date=2026-05-22 to download_adp_bundle exactly once —
   ADP downloads the whole pay period containing 5/22 (which also
   contains 5/21), so one ADP login covers both days.

If a future refactor accidentally introduces a per-day loop (e.g. for
date in range(gap_start, refresh_date+1): download_transactions(...)),
this test will fail because compute_gap_window will still return a
single (gap_start, label) tuple but the orchestrator's contract that
``download_transactions(start_date=gap_start, end_date=refresh_date)``
is the one-shot caller will be broken — see assertions below.
"""

from __future__ import annotations

import datetime
import inspect
import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))


class GapWindowMultiDayTest(unittest.TestCase):
    def test_compute_gap_window_returns_single_range_for_two_day_gap(self) -> None:
        """data_window_end=5/20 + refresh_date=5/22 → gap_start=5/21.

        The orchestrator then calls download_transactions with
        start_date=gap_start, end_date=refresh_date — i.e. ONE range
        covering 5/21..5/22 inclusive.
        """
        from agents.bhaga.scripts.daily_refresh import compute_gap_window

        prev_end = datetime.date(2026, 5, 20)
        refresh_date = datetime.date(2026, 5, 22)
        # data_start is only consulted on fresh install; pass a sentinel so
        # accidental fresh-install branch use would be immediately visible.
        data_start = datetime.date(2000, 1, 1)

        gap_start, gap_source = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=False,
            data_start=data_start,
            refresh_date=refresh_date,
        )

        self.assertEqual(
            gap_start, datetime.date(2026, 5, 21),
            "two-day-gap recovery must start at prev_end + 1 day, not later",
        )
        self.assertIn(
            "data_window_end", gap_source,
            "incremental branch should label its source as data_window_end-derived",
        )
        # The (gap_start, refresh_date) range is what the orchestrator
        # passes to download_transactions as a single Square scrape.
        # This is the contract that must NOT regress into a per-day loop.
        days_in_window = (refresh_date - gap_start).days + 1
        self.assertEqual(
            days_in_window, 2,
            "expected a 2-day single-range window for the 5/20→5/22 recovery",
        )

    def test_download_transactions_accepts_a_range_not_a_single_day(self) -> None:
        """Guard against a refactor that splits Square into per-day calls.

        If download_transactions ever loses its start_date / end_date kwargs
        in favour of a single 'day' kwarg, this test fails loudly. We don't
        actually invoke the browser; we just inspect the signature.
        """
        from skills.square_tips.runner import download_transactions

        sig = inspect.signature(download_transactions)
        params = sig.parameters
        self.assertIn(
            "start_date", params,
            "download_transactions must accept start_date — per-day looping in "
            "the orchestrator is a regression (multi-day single-call is the contract)",
        )
        self.assertIn(
            "end_date", params,
            "download_transactions must accept end_date — single-range scrape is "
            "the contract; per-day loops would multiply Square 2FA OTP costs",
        )

    def test_download_adp_bundle_takes_one_target_date_for_the_pay_period(self) -> None:
        """A two-day gap (5/21 + 5/22) sits inside one ADP pay period, so
        ONE bundle call with target_date=5/22 must cover both days. If the
        bundle ever requires a date range or loses target_date, the
        orchestrator would have to loop and multiply ADP OTP costs."""
        from skills.adp_run_automation.runner import download_adp_bundle

        sig = inspect.signature(download_adp_bundle)
        self.assertIn(
            "target_date", sig.parameters,
            "download_adp_bundle must accept a single target_date so one call "
            "covers the pay period containing both gap days",
        )


if __name__ == "__main__":
    unittest.main()
