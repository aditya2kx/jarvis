"""Regression test: a recovery run after a multi-day outage must cover
a single CONTIGUOUS date range, not a per-day loop.

Scenario (2026-05-22 IST-truncation incident recovery): the model sheet's
``data_window_end`` was reset to 2026-05-20 because 5/21 + 5/22 data had
been polluted by an IST-clipped scrape. The orchestrator then ran with
``refresh_date=2026-05-22``. It MUST:

1. Resolve gap_start = 2026-05-21 (data_window_end + 1 day).
2. Pass (start_date=2026-05-21, end_date=2026-05-22) as a SINGLE range to
   ingest_window (Square API) — i.e. one API call for the window.
3. Pass target_date=2026-05-22 to download_adp_bundle exactly once —
   ADP downloads the whole pay period containing 5/22 (which also
   contains 5/21), so one ADP login covers both days.
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

    def test_ingest_window_accepts_a_range_not_a_single_day(self) -> None:
        """Guard against a refactor that splits Square API ingest into per-day calls.

        ingest_window(start_date, end_date) must accept a range — per-day looping
        would multiply API calls and break the multi-day recovery contract.
        """
        from skills.square_api.ingest import ingest_window

        sig = inspect.signature(ingest_window)
        params = sig.parameters
        self.assertIn(
            "start_date", params,
            "ingest_window must accept start_date — per-day looping is a regression",
        )
        self.assertIn(
            "end_date", params,
            "ingest_window must accept end_date — single-range call is the contract",
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
