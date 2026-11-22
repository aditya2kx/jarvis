"""Austin and Houston share one pipeline image; each store's data stays in its own targets."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from agents.bhaga import notify
from agents.bhaga.scripts import daily_refresh as dr
from agents.bhaga.scripts import ingest_inventory
from agents.bhaga.scripts import materialize_model_bq as mm
from skills.adp_run_automation import runner as adp_runner
from skills.square_api import auth as square_auth

HOUSTON_ENV = {
    "BHAGA_BQ_DATASET": "bhaga_houston",
    "BHAGA_FIRESTORE_COLLECTION": "runs_houston",
    "BHAGA_GCS_CACHE_BUCKET": "bhaga-scrape-cache-houston",
}
TARGET_VARS = ("BHAGA_BQ_DATASET", "BHAGA_FIRESTORE_COLLECTION", "BHAGA_GCS_CACHE_BUCKET",
               "BHAGA_GCS_CACHE_WRITE_BUCKET", "BHAGA_SHEET_MODE")


def _env(**overrides):
    base = {k: v for k, v in os.environ.items() if k not in TARGET_VARS}
    return mock.patch.dict(os.environ, {**base, **overrides}, clear=True)


class StoreTargets(unittest.TestCase):
    def test_austin_with_default_targets_runs(self):
        with _env():
            dr._assert_store_targets("palmetto")

    def test_houston_with_its_own_targets_runs(self):
        with _env(**HOUSTON_ENV):
            dr._assert_store_targets("houston")

    def test_houston_against_austin_dataset_is_refused(self):
        with _env(**{**HOUSTON_ENV, "BHAGA_BQ_DATASET": "bhaga"}):
            with self.assertRaises(SystemExit) as cm:
                dr._assert_store_targets("houston")
        self.assertIn("store_target_mismatch store=houston dataset=bhaga expected=bhaga_houston",
                      str(cm.exception))

    def test_austin_against_houston_targets_is_refused(self):
        with _env(**HOUSTON_ENV):
            with self.assertRaises(SystemExit) as cm:
                dr._assert_store_targets("palmetto")
        msg = str(cm.exception)
        for part in ("dataset=bhaga_houston", "collection=runs_houston",
                     "bucket=bhaga-scrape-cache-houston"):
            self.assertIn(part, msg)

    def test_houston_writing_into_austins_cache_is_refused(self):
        with _env(**{**HOUSTON_ENV, "BHAGA_GCS_CACHE_WRITE_BUCKET": "bhaga-scrape-cache"}):
            with self.assertRaises(SystemExit) as cm:
                dr._assert_store_targets("houston")
        self.assertIn("write_bucket=bhaga-scrape-cache", str(cm.exception))

    def test_sandbox_runs_keep_their_own_guards(self):
        with _env(BHAGA_SHEET_MODE="staging", BHAGA_BQ_DATASET="bhaga_sandbox"):
            dr._assert_store_targets("palmetto")

    def test_refused_run_records_no_pipeline_run(self):
        with _env(**{**HOUSTON_ENV, "BHAGA_STORE": "houston", "BHAGA_BQ_DATASET": "bhaga"}), \
                mock.patch.object(dr.sys, "argv", ["daily_refresh", "--store", "palmetto"]), \
                mock.patch.object(dr, "_record_pipeline_run") as record, \
                mock.patch.dict(dr._RUN_SUMMARY, {}, clear=True):
            with self.assertRaises(SystemExit):
                dr.main()
        self.assertIsNone(dr._RUN_SUMMARY.get("refresh_date"))
        self.assertFalse(dr._should_record_pipeline_run())
        record.assert_called_once()


class SquareSecret(unittest.TestCase):
    def test_both_stores_use_the_one_merchant_secret(self):
        self.assertEqual(square_auth._secret_name("palmetto"), "square_palmetto_oauth")
        self.assertEqual(square_auth._secret_name("houston"), "square_palmetto_oauth")

    def test_store_without_a_profile_keeps_the_naming_rule(self):
        self.assertEqual(square_auth._secret_name("dallas"), "square_dallas_oauth")


class AdpCredential(unittest.TestCase):
    def test_houston_logs_in_with_the_shared_austin_login(self):
        self.assertEqual(adp_runner._adp_credential_name("houston"), "adp_palmetto_login")

    def test_austin_credential_is_unchanged(self):
        self.assertEqual(adp_runner._adp_credential_name("palmetto"), "adp_palmetto_login")

    def test_store_without_a_profile_keeps_the_naming_rule(self):
        self.assertEqual(adp_runner._adp_credential_name("nosuchstore"), "adp_nosuchstore_login")


class ClosingFormSource(unittest.TestCase):
    def test_austin_reads_its_closing_list(self):
        self.assertEqual(ingest_inventory._closing_list_id("palmetto"), "901711373136")

    def test_houston_without_a_closing_list_ingests_nothing(self):
        with mock.patch.object(ingest_inventory, "list_tasks") as lt, \
                mock.patch.object(ingest_inventory, "_get_high_water_ts_ms", return_value=None):
            self.assertEqual(ingest_inventory.ingest("houston", dry_run=True), 0)
        lt.assert_not_called()


class SlackStoreLabel(unittest.TestCase):
    def test_austin_messages_are_unchanged(self):
        with _env(BHAGA_STORE="palmetto"):
            self.assertEqual(notify._run_prefix(), "")
        with _env():
            self.assertEqual(notify._run_prefix(), "")

    def test_houston_messages_name_the_store(self):
        with _env(BHAGA_STORE="houston"):
            self.assertEqual(notify._run_prefix(), "*[Houston]* ")


class PreOpeningModel(unittest.TestCase):
    def _materialize(self, dates):
        empty = mock.MagicMock(return_value=[])
        with mock.patch.multiple(
            mm, read_shifts_bq=empty, read_punches_bq=empty, read_transactions_bq=empty,
            read_wage_rates_bq=empty, read_item_daily_bq=empty, read_kds_daily_bq=empty,
            load_rows=mock.DEFAULT,
        ) as patched:
            mm.materialize("houston", dates=dates)
        return patched["load_rows"]

    def test_no_sales_before_opening_skips_without_writing(self):
        self.assertFalse(self._materialize(["2026-11-14", "2026-11-15"]).called)

    def test_full_history_rebuild_uses_today_before_opening(self):
        fake_dt = mock.MagicMock()
        fake_dt.datetime.now.return_value.date.return_value.isoformat.return_value = "2026-11-16"
        with mock.patch.object(mm, "datetime", fake_dt):
            self.assertFalse(self._materialize(None).called)

    def test_no_sales_after_opening_still_fails(self):
        with self.assertRaisesRegex(RuntimeError, "square_transactions` is empty"):
            self._materialize(["2026-11-22", "2026-11-23"])

    def test_store_without_opening_date_still_fails(self):
        with mock.patch.multiple(
            mm, read_shifts_bq=mock.DEFAULT, read_punches_bq=mock.DEFAULT,
            read_transactions_bq=mock.DEFAULT, read_wage_rates_bq=mock.DEFAULT,
            read_item_daily_bq=mock.DEFAULT, read_kds_daily_bq=mock.DEFAULT,
        ) as patched:
            for m in patched.values():
                m.return_value = []
            with self.assertRaisesRegex(RuntimeError, "square_transactions` is empty"):
                mm.materialize("palmetto", dates=["2026-11-15"])


if __name__ == "__main__":
    unittest.main()
