#!/usr/bin/env python3
"""Tests for the staging/production sheet-isolation mechanism.

Verifies that when BHAGA_SHEET_MODE=staging, resolve_sheet_id routes to
staging IDs and any attempt to touch a production sheet ID raises
RuntimeError. This guard is generic infrastructure for future migrations.

NOTE: As of the 2026-05-30 cutover, palmetto.json no longer carries a
``google_sheets_staging`` block and the live job runs in plain prod mode
(no BHAGA_SHEET_MODE). These tests therefore use a SYNTHETIC profile and a
synthetic production-ID set so they exercise the mechanism without
depending on retired config data.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import config_loader


PROD_IDS = {
    "bhaga_model": "PROD_model_0000000000000000000000000000",
    "bhaga_adp_raw": "PROD_adp_000000000000000000000000000000",
    "bhaga_square_raw": "PROD_square_0000000000000000000000000000",
    "bhaga_review_raw": "PROD_review_0000000000000000000000000000",
}
STAGING_IDS = {
    "bhaga_model": "STG_model_00000000000000000000000000000",
    "bhaga_adp_raw": "STG_adp_0000000000000000000000000000000",
    "bhaga_square_raw": "STG_square_000000000000000000000000000",
    "bhaga_review_raw": "STG_review_000000000000000000000000000",
}


def _synthetic_profile() -> dict:
    return {
        "google_sheets": {
            k: {"spreadsheet_id": v} for k, v in PROD_IDS.items()
        },
        "google_sheets_staging": {
            k: {"spreadsheet_id": v} for k, v in STAGING_IDS.items()
        },
    }


class TestStagingIsolation(unittest.TestCase):
    """When BHAGA_SHEET_MODE=staging, production sheets must be blocked."""

    def setUp(self):
        # Pin the production-ID block-list to our synthetic set so the guard
        # is independent of what the real store-profiles currently contain.
        config_loader._PRODUCTION_SHEET_IDS = frozenset(PROD_IDS.values())

    def tearDown(self):
        os.environ.pop("BHAGA_SHEET_MODE", None)
        config_loader._PRODUCTION_SHEET_IDS = None

    def test_production_sheet_blocked_in_staging_mode(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        for sid in PROD_IDS.values():
            with self.assertRaises(RuntimeError, msg=f"Should block prod sheet {sid}"):
                config_loader._assert_not_production_sheet(sid)

    def test_staging_sheet_allowed_in_staging_mode(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        for sid in STAGING_IDS.values():
            config_loader._assert_not_production_sheet(sid)

    def test_production_sheet_allowed_in_prod_mode(self):
        os.environ.pop("BHAGA_SHEET_MODE", None)
        for sid in PROD_IDS.values():
            config_loader._assert_not_production_sheet(sid)

    def test_production_sheet_allowed_when_mode_is_prod(self):
        os.environ["BHAGA_SHEET_MODE"] = "prod"
        for sid in PROD_IDS.values():
            config_loader._assert_not_production_sheet(sid)

    def test_resolve_sheet_id_routes_to_staging(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        profile = _synthetic_profile()
        staging = set(STAGING_IDS.values())
        prod = set(PROD_IDS.values())
        for key in ("bhaga_model", "bhaga_adp_raw", "bhaga_square_raw", "bhaga_review_raw"):
            sid = config_loader.resolve_sheet_id(key, profile)
            self.assertIn(sid, staging, f"{key} resolved to {sid} which is NOT a staging ID")
            self.assertNotIn(sid, prod, f"{key} resolved to {sid} which IS a production ID")

    def test_resolve_sheet_id_prod_mode_returns_prod(self):
        os.environ.pop("BHAGA_SHEET_MODE", None)
        profile = _synthetic_profile()
        for key, expected in PROD_IDS.items():
            self.assertEqual(config_loader.resolve_sheet_id(key, profile), expected)

    def test_resolve_sheet_id_blocks_if_staging_missing(self):
        """If staging IDs are missing and fallback hits production, guard fires."""
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        profile_no_staging = {"google_sheets": _synthetic_profile()["google_sheets"]}
        with self.assertRaises(RuntimeError):
            config_loader.resolve_sheet_id("bhaga_model", profile_no_staging)

    def test_error_message_includes_sheet_id(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        sid = next(iter(PROD_IDS.values()))
        try:
            config_loader._assert_not_production_sheet(sid)
            self.fail("Should have raised RuntimeError")
        except RuntimeError as e:
            self.assertIn(sid, str(e))
            self.assertIn("BLOCKED", str(e))
            self.assertIn("staging", str(e).lower())


if __name__ == "__main__":
    unittest.main()
