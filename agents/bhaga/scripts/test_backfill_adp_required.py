#!/usr/bin/env python3
"""Tests for the ADP empty-load guard and BQ load receipts (Issue #305).

On 2026-09-14 a nightly scraped ADP, set its Firestore marker, then failed at
load_raw_bigquery. The rerun saw the marker, skipped the scrape, found an empty
extracted/downloads/ in a fresh container, upserted nothing, and exited 0 — so
load_raw_bigquery.done was written over an empty load and the run reported
success. Two things have to hold to make that impossible:

  1. A load asked for ADP sources that finds none of them must exit non-zero.
  2. Each ADP export that IS parsed must leave a durable BQ receipt, since the
     receipt (not the container-local file, and not the Firestore marker) is
     what the scrape gate consults.
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import backfill_from_downloads as bfd


class TestAdpInputsAbsent(unittest.TestCase):
    def test_requested_but_nothing_loaded_is_a_failure(self):
        self.assertTrue(
            bfd.adp_inputs_absent(["square"], set(), require_adp=True),
        )

    def test_all_adp_skipped_is_not_a_failure(self):
        skip = ["square", "adp_shifts", "adp_schedule", "adp_liability", "adp_rates"]
        self.assertFalse(bfd.adp_inputs_absent(skip, set(), require_adp=True))

    def test_one_export_loaded_is_enough(self):
        """A single legitimately-absent export must not fail the nightly.

        Schedules are forward-looking and rates are pay-period cadenced, so
        demanding all four would fail on ordinary nights.
        """
        self.assertFalse(
            bfd.adp_inputs_absent(["square"], {"adp_timecard"}, require_adp=True),
        )

    def test_unanswered_otp_stays_a_graceful_skip(self):
        """`bhaga.mdc`: on no OTP reply the ADP step is skipped and the run
        still exits 0. Without --require-adp an empty ADP load must pass."""
        self.assertFalse(bfd.adp_inputs_absent(["square"], set()))

    def test_requested_set_excludes_skipped(self):
        self.assertEqual(
            bfd.adp_sources_requested(["square", "adp_rates", "adp_liability"]),
            {"adp_shifts", "adp_schedule"},
        )


class TestLoadReceipts(unittest.TestCase):
    """Receipts must be durable, idempotent, and never able to break a load.

    These exercise the real `record_load_receipt`, not a re-implementation of
    it — a fixture that merely mirrors the production logic is how the
    2026-09-14 FLOAT bug shipped green.
    """

    RD = datetime.date(2026, 9, 14)

    def _record(self, *, rows=104, refresh_date=RD, dry_run=False,
                side_effect=None):
        loaded: set[str] = set()
        with mock.patch.object(bfd, "_ds_load_rows",
                               side_effect=side_effect) as load:
            bfd.record_load_receipt(
                "adp_timecard",
                store="palmetto",
                refresh_date=refresh_date,
                rows=rows,
                loaded_sources=loaded,
                dry_run=dry_run,
            )
        return load, loaded

    def test_receipt_merges_on_store_date_source(self):
        load, _ = self._record()
        table, rows = load.call_args.args
        self.assertEqual(table, "source_load_receipts")
        self.assertEqual(
            load.call_args.kwargs["merge_keys"],
            ["store", "refresh_date", "source"],
        )
        self.assertEqual(rows[0]["source"], "adp_timecard")
        self.assertEqual(rows[0]["refresh_date"], "2026-09-14")

    def test_zero_rows_still_writes_a_receipt(self):
        """A store-closed day must leave a receipt or the gate re-scrapes it
        every night, re-prompting for ADP OTP each time."""
        load, _ = self._record(rows=0)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(load.call_args.args[1][0]["rows_upserted"], 0)

    def test_receipt_failure_is_swallowed(self):
        load, loaded = self._record(side_effect=RuntimeError("BQ down"))
        self.assertIn("adp_timecard", loaded)   # must not raise

    def test_dry_run_writes_nothing(self):
        load, loaded = self._record(dry_run=True)
        load.assert_not_called()
        self.assertIn("adp_timecard", loaded)

    def test_source_tracking_survives_a_missing_refresh_date(self):
        """Without --refresh-date there is no receipt, but the empty-load guard
        still needs to know the export was present."""
        load, loaded = self._record(refresh_date=None)
        load.assert_not_called()
        self.assertFalse(bfd.adp_inputs_absent(["square"], loaded))


class TestReceiptsBypassReplaceTruncation(unittest.TestCase):
    """Receipts must never go through the module load_rows wrapper.

    In --replace (fresh full-history scrape) mode that wrapper injects
    replace=True, which TRUNCATEs the target — wiping every other date's
    receipt and re-arming the exact silent-skip this table exists to prevent.
    """

    def test_replace_mode_does_not_truncate_the_receipts_table(self):
        loaded: set[str] = set()
        try:
            bfd._REPLACE_TABLES = True
            with mock.patch.object(bfd, "_ds_load_rows") as load:
                bfd.record_load_receipt(
                    "adp_timecard",
                    store="palmetto",
                    refresh_date=datetime.date(2026, 9, 14),
                    rows=1,
                    loaded_sources=loaded,
                )
        finally:
            bfd._REPLACE_TABLES = False
        self.assertNotIn("replace", load.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
