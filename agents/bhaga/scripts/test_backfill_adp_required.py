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

    def test_the_wrapper_would_have_truncated_it(self):
        """The bypass is load-bearing, not incidental.

        Asserting only that `record_load_receipt` omits `replace` is vacuous —
        it calls `_ds_load_rows` directly, so `replace` could never appear no
        matter what `_REPLACE_TABLES` said. What makes the bypass necessary is
        that the module wrapper WOULD have injected it for this same call, so
        pin that: if this assertion ever fails the wrapper stopped truncating
        and the bypass (plus the test above) is dead weight.
        """
        try:
            bfd._REPLACE_TABLES = True
            with mock.patch.object(bfd, "_ds_load_rows") as load:
                bfd.load_rows("source_load_receipts", [{"store": "palmetto"}],
                              merge_keys=["store", "refresh_date", "source"])
        finally:
            bfd._REPLACE_TABLES = False
        self.assertTrue(load.call_args.kwargs.get("replace"))


class TestTableIsolation(unittest.TestCase):
    """Issue #338: a non-tip table failing must not cost the day its tips.

    Drives the real `main()` with a timecard and a schedule on disk. The
    schedule parser is made to fail; the timecard must still load and the exit
    must be EXIT_PARTIAL with the failure written to --result-json.
    """

    def _run(self, *, schedule_error=None, shifts_error=None):
        import json
        import pathlib
        import tempfile

        tmp = pathlib.Path(tempfile.mkdtemp())
        (tmp / "Timecard-2026-09-23.xlsx").write_bytes(b"")
        (tmp / "Schedule-2026-09-23.json").write_text(json.dumps({"weeks": []}))
        result_json = tmp / "result.json"
        loaded: list[str] = []

        def _load(table, rows, **_kw):
            if table == "adp_shifts" and shifts_error:
                raise shifts_error
            loaded.append(table)
            return len(rows)

        argv = ["backfill_from_downloads", "--store", "palmetto", "--skip", "square",
                "--skip", "adp_liability", "--skip", "adp_rates",
                "--refresh-date", "2026-09-22", "--require-adp",
                "--result-json", str(result_json)]
        punch = {"date": "2026-09-22"}
        with mock.patch.dict(os.environ, {"BHAGA_DATASTORE": "bigquery"}), \
             mock.patch.object(sys, "argv", argv), \
             mock.patch.object(bfd, "DOWNLOADS", tmp), \
             mock.patch.object(bfd, "load_store_profile", return_value={
                 "timezone": {"shop_tz": "America/Chicago"}, "google_account_key": "x"}), \
             mock.patch.object(bfd, "resolve_sheet_id", return_value="sid"), \
             mock.patch("skills.store_profile.load_aliases", return_value={}), \
             mock.patch("skills.store_profile.load_exclusions",
                        return_value={"permanent": []}), \
             mock.patch.object(bfd.shift_backend, "parse_xlsx", return_value=[punch]), \
             mock.patch.object(bfd.shift_backend, "aggregate_by_day", return_value=[punch]), \
             mock.patch.object(bfd, "detect_new_employees", return_value=[]), \
             mock.patch.object(bfd, "map_adp_shift", side_effect=lambda r: dict(r)), \
             mock.patch.object(bfd, "map_adp_punch", side_effect=lambda r: dict(r)), \
             mock.patch.object(bfd.schedule_backend, "build_schedule_records",
                               side_effect=schedule_error, return_value=[]), \
             mock.patch.object(bfd.schedule_backend, "build_employee_schedule_records",
                               return_value=[]), \
             mock.patch.object(bfd.schedule_backend, "reconcile_employee_vs_footer",
                               return_value=[]), \
             mock.patch.object(bfd, "load_rows", side_effect=_load), \
             mock.patch.object(bfd, "_ds_load_rows"):
            rc = bfd.main()
        failures = (json.loads(result_json.read_text())["failures"]
                    if result_json.exists() else None)
        return rc, loaded, failures

    def test_clean_load_exits_zero_and_writes_no_result(self):
        rc, loaded, failures = self._run()
        self.assertEqual(rc, 0)
        self.assertIsNone(failures)
        self.assertIn("adp_shifts", loaded)

    def test_isolated_failure_is_partial_and_keeps_the_timecard(self):
        rc, loaded, failures = self._run(schedule_error=RuntimeError("schedule boom"))
        self.assertEqual(rc, bfd.EXIT_PARTIAL)
        self.assertEqual(loaded, ["adp_shifts", "adp_punches"])
        self.assertEqual(failures[0]["source"], "adp_schedule")
        self.assertIn("RuntimeError: schedule boom", failures[0]["error"])

    def test_tip_critical_failure_stays_fatal(self):
        """Tips are computed from shifts/punches, so they are never isolated."""
        with self.assertRaises(RuntimeError):
            self._run(shifts_error=RuntimeError("shifts boom"))


if __name__ == "__main__":
    unittest.main()
