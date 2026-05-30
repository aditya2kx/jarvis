"""E2E: backfill_item_lines_from_cache parses fixtures and upserts (mocked)."""

from __future__ import annotations

import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "item_sales"


class BackfillItemLinesCacheE2ETests(unittest.TestCase):
    @mock.patch("agents.bhaga.scripts.backfill_item_lines_from_cache.write_raw_square_item_lines")
    @mock.patch("agents.bhaga.scripts.backfill_item_lines_from_cache.resolve_sheet_id")
    @mock.patch("agents.bhaga.scripts.backfill_item_lines_from_cache.load_store_profile")
    def test_local_fixture_upsert(self, mock_profile, mock_sid, mock_write):
        mock_profile.return_value = {
            "timezone": {"shop_tz": "America/Chicago"},
        }
        mock_sid.return_value = "fake-square-raw"
        mock_write.return_value = {
            "inserted": 3, "updated": 0, "total_after": 3,
        }

        from agents.bhaga.scripts import backfill_item_lines_from_cache as mod

        argv = [
            "backfill_item_lines_from_cache",
            "--store", "palmetto",
            "--local-only",
            "--download-dir", str(_FIXTURE_DIR),
        ]
        with mock.patch.object(sys, "argv", argv):
            rc = mod.main()

        self.assertEqual(rc, 0)
        self.assertTrue(mock_write.called)
        all_records: list[dict] = []
        for call in mock_write.call_args_list:
            all_records.extend(call[0][1])
        self.assertEqual(len(all_records), 3)
        keys = {
            (r["transaction_id"], r["item_name"], r["item_sold_at_local"], r["line_seq"])
            for r in all_records
        }
        self.assertEqual(len(keys), 3)

    @mock.patch("agents.bhaga.scripts.backfill_item_lines_from_cache.write_raw_square_item_lines")
    @mock.patch("agents.bhaga.scripts.backfill_item_lines_from_cache.load_store_profile")
    def test_dry_run_reports_coverage(self, mock_profile, mock_write):
        mock_profile.return_value = {"timezone": {"shop_tz": "America/Chicago"}}

        from agents.bhaga.scripts import backfill_item_lines_from_cache as mod

        argv = [
            "backfill_item_lines_from_cache",
            "--store", "palmetto",
            "--dry-run",
            "--local-only",
            "--download-dir", str(_FIXTURE_DIR),
        ]
        with mock.patch.object(sys, "argv", argv):
            rc = mod.main()

        self.assertEqual(rc, 0)
        mock_write.assert_not_called()

    @mock.patch("agents.bhaga.scripts.backfill_item_lines_from_cache.load_store_profile")
    def test_default_never_scans_local_downloads(self, mock_profile):
        mock_profile.return_value = {"timezone": {"shop_tz": "America/Chicago"}}

        from agents.bhaga.scripts import backfill_item_lines_from_cache as mod

        argv = [
            "backfill_item_lines_from_cache",
            "--store", "palmetto",
            "--dry-run",
        ]
        with mock.patch.object(sys, "argv", argv):
            with mock.patch.object(mod, "_iter_local_item_csvs") as mock_local:
                with mock.patch.object(
                    mod, "_iter_gcs_item_csvs", return_value=iter([_FIXTURE_DIR / "items-sample.csv"])
                ) as mock_gcs:
                    rc = mod.main()

        self.assertEqual(rc, 0)
        mock_local.assert_not_called()
        mock_gcs.assert_called_once()
        self.assertEqual(mock_gcs.call_args.kwargs.get("required"), True)


if __name__ == "__main__":
    unittest.main()
