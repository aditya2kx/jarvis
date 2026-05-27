#!/usr/bin/env python3
"""Tests for staging/production sheet isolation.

Verifies that when BHAGA_SHEET_MODE=staging, any attempt to access a
production sheet ID raises RuntimeError. This is the hard guard that
prevents the cloud flow from reading or writing production data.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import config_loader


PROFILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "agents", "bhaga", "knowledge-base", "store-profiles", "palmetto.json",
)


def _load_prod_ids() -> set:
    with open(PROFILE_PATH) as f:
        profile = json.load(f)
    ids = set()
    for sheet_info in profile.get("google_sheets", {}).values():
        if isinstance(sheet_info, dict) and "spreadsheet_id" in sheet_info:
            ids.add(sheet_info["spreadsheet_id"])
    return ids


def _load_staging_ids() -> set:
    with open(PROFILE_PATH) as f:
        profile = json.load(f)
    ids = set()
    for sheet_info in profile.get("google_sheets_staging", {}).values():
        if isinstance(sheet_info, dict) and "spreadsheet_id" in sheet_info:
            ids.add(sheet_info["spreadsheet_id"])
    return ids


class TestStagingIsolation(unittest.TestCase):
    """When BHAGA_SHEET_MODE=staging, production sheets must be blocked."""

    def setUp(self):
        config_loader._PRODUCTION_SHEET_IDS = None

    def tearDown(self):
        os.environ.pop("BHAGA_SHEET_MODE", None)
        config_loader._PRODUCTION_SHEET_IDS = None

    def test_production_sheet_blocked_in_staging_mode(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        prod_ids = _load_prod_ids()
        self.assertTrue(len(prod_ids) >= 4, f"Expected >=4 prod sheet IDs, got {len(prod_ids)}")
        for sid in prod_ids:
            with self.assertRaises(RuntimeError, msg=f"Should block prod sheet {sid}"):
                config_loader._assert_not_production_sheet(sid)

    def test_staging_sheet_allowed_in_staging_mode(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        staging_ids = _load_staging_ids()
        self.assertTrue(len(staging_ids) >= 4, f"Expected >=4 staging sheet IDs, got {len(staging_ids)}")
        for sid in staging_ids:
            config_loader._assert_not_production_sheet(sid)

    def test_production_sheet_allowed_in_prod_mode(self):
        os.environ.pop("BHAGA_SHEET_MODE", None)
        prod_ids = _load_prod_ids()
        for sid in prod_ids:
            config_loader._assert_not_production_sheet(sid)

    def test_production_sheet_allowed_when_mode_is_prod(self):
        os.environ["BHAGA_SHEET_MODE"] = "prod"
        prod_ids = _load_prod_ids()
        for sid in prod_ids:
            config_loader._assert_not_production_sheet(sid)

    def test_resolve_sheet_id_routes_to_staging(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        with open(PROFILE_PATH) as f:
            profile = json.load(f)
        staging_ids = _load_staging_ids()
        prod_ids = _load_prod_ids()
        for key in ("bhaga_model", "bhaga_adp_raw", "bhaga_square_raw", "bhaga_review_raw"):
            sid = config_loader.resolve_sheet_id(key, profile)
            self.assertIn(sid, staging_ids, f"{key} resolved to {sid} which is NOT a staging ID")
            self.assertNotIn(sid, prod_ids, f"{key} resolved to {sid} which IS a production ID")

    def test_resolve_sheet_id_blocks_if_staging_missing(self):
        """If staging IDs are missing and fallback hits production, guard fires."""
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        with open(PROFILE_PATH) as f:
            profile = json.load(f)
        profile_no_staging = dict(profile)
        profile_no_staging.pop("google_sheets_staging", None)
        with self.assertRaises(RuntimeError):
            config_loader.resolve_sheet_id("bhaga_model", profile_no_staging)

    def test_no_overlap_between_prod_and_staging_ids(self):
        prod_ids = _load_prod_ids()
        staging_ids = _load_staging_ids()
        overlap = prod_ids & staging_ids
        self.assertEqual(overlap, set(), f"Prod and staging share IDs: {overlap}")

    def test_error_message_includes_sheet_id(self):
        os.environ["BHAGA_SHEET_MODE"] = "staging"
        prod_ids = _load_prod_ids()
        sid = next(iter(prod_ids))
        try:
            config_loader._assert_not_production_sheet(sid)
            self.fail("Should have raised RuntimeError")
        except RuntimeError as e:
            self.assertIn(sid, str(e))
            self.assertIn("BLOCKED", str(e))
            self.assertIn("staging", str(e).lower())


if __name__ == "__main__":
    unittest.main()
