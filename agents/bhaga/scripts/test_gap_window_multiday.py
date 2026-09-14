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

    def test_scoped_model_write_covers_every_ingested_day(self) -> None:
        """Issue #295: the model scope must be as wide as the ingest window.

        Same class of bug this file already guards, one layer down: ingest
        correctly covers gap_start..refresh_date as a range, but the scoped
        model write was handed only ``refresh_date``. Under
        BHAGA_SCOPED_MATERIALIZE that leaves fresh raw data under stale model
        rows for every day of the gap except the last — and it is invisible,
        because each table still holds exactly one row per key.
        """
        from agents.bhaga.scripts.daily_refresh import ingested_dates

        gap_start = datetime.date(2026, 9, 7)
        refresh_date = datetime.date(2026, 9, 13)

        dates = ingested_dates(gap_start, refresh_date)

        self.assertEqual(
            dates,
            ["2026-09-07", "2026-09-08", "2026-09-09",
             "2026-09-10", "2026-09-11", "2026-09-12", "2026-09-13"],
            "a 7-day catch-up must scope the model write to all 7 ingested days",
        )
        self.assertEqual(
            len(dates), (refresh_date - gap_start).days + 1,
            "scope width must equal the ingest window width, inclusive",
        )

    def test_steady_state_nightly_scopes_to_the_single_day(self) -> None:
        """No gap → exactly one date. Widening must not leak into the common case."""
        from agents.bhaga.scripts.daily_refresh import ingested_dates

        day = datetime.date(2026, 9, 14)
        self.assertEqual(ingested_dates(day, day), ["2026-09-14"])

    def test_source_overrides_widen_the_scope_they_never_narrow_it(self) -> None:
        """--square-from/--adp-to can reach outside the gap window.

        Those windows are what actually gets ingested, so the scope has to
        follow them. A source window that falls *inside* the gap must not
        shrink the scope — narrowing is the failure mode, not widening.
        """
        from agents.bhaga.scripts.daily_refresh import ingested_dates

        gap_start = datetime.date(2026, 9, 12)
        refresh_date = datetime.date(2026, 9, 13)

        widened = ingested_dates(
            gap_start,
            refresh_date,
            extra_windows=(
                (datetime.date(2026, 9, 10), None),   # --square-from reaches back
                (None, datetime.date(2026, 9, 15)),   # --adp-to reaches forward
            ),
        )
        self.assertEqual(widened[0], "2026-09-10", "earlier source window must widen the start")
        self.assertEqual(widened[-1], "2026-09-15", "later source window must widen the end")

        unchanged = ingested_dates(
            gap_start,
            refresh_date,
            extra_windows=((datetime.date(2026, 9, 13), datetime.date(2026, 9, 13)),),
        )
        self.assertEqual(
            unchanged, ["2026-09-12", "2026-09-13"],
            "a source window inside the gap must not narrow the scope",
        )

    def test_empty_gap_still_materializes_the_refresh_date(self) -> None:
        """gap_start after refresh_date means nothing to scrape — but the run
        still has to write the day it ran for, never an empty scope (which
        materialize_model_bq would treat as 'no rows' and skip)."""
        from agents.bhaga.scripts.daily_refresh import ingested_dates

        dates = ingested_dates(
            datetime.date(2026, 9, 14), datetime.date(2026, 9, 13),
        )
        self.assertEqual(dates, ["2026-09-13"])

    def test_orchestrator_passes_the_window_not_just_refresh_date(self) -> None:
        """Guard the call site itself.

        ``ingested_dates`` being correct is worthless if the orchestrator goes
        back to ``--dates refresh_date.isoformat()``, which is exactly the line
        #295 was about.
        """
        source = (PROJECT_ROOT / "agents/bhaga/scripts/daily_refresh.py").read_text()
        # The argv list literal handed to materialize_model_bq.
        argv = source.split("agents.bhaga.scripts.materialize_model_bq", 1)[1]
        argv = argv.split("]", 1)[0]

        self.assertIn(
            "model_dates", argv,
            "materialize_model_bq must receive the joined ingest window",
        )
        self.assertNotIn(
            "refresh_date.isoformat()", argv,
            "passing only refresh_date is the Issue #295 regression",
        )
        self.assertIn(
            "model_dates = ingested_dates(", source,
            "the window must come from ingested_dates, not be rebuilt inline",
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
