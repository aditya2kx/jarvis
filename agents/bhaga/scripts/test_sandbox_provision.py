#!/usr/bin/env python3
"""Offline tests for sandbox_provision — no Google API calls."""

import datetime
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import sandbox_provision as sp

_REGISTRY = {
    "store": "palmetto",
    "num_slots": 3,
    "folder_id": "FOLDER",
    "slots": [
        {**{k: f"s0_{k}" for k in sp.PROFILE_KEYS}, "slot": 0},
        {**{k: f"s1_{k}" for k in sp.PROFILE_KEYS}, "slot": 1},
        {**{k: f"s2_{k}" for k in sp.PROFILE_KEYS}, "slot": 2},
    ],
}

_POINTER = {
    "google_account_key": "palmetto",
    "google_sheets": {"bhaga_model": {"spreadsheet_id": "PROD_MODEL"}},
}


class TestPureHelpers(unittest.TestCase):
    def test_staging_env_key(self):
        self.assertEqual(sp.staging_env_key("bhaga_model"), "BHAGA_STAGING_BHAGA_MODEL_SID")

    def test_slot_title_is_slot_scoped(self):
        self.assertEqual(sp.slot_title(1, "bhaga_model"), "BHAGA-sandbox slot1 bhaga_model")
        self.assertNotEqual(sp.slot_title(0, "bhaga_model"), sp.slot_title(1, "bhaga_model"))

    def test_all_slot_titles_covers_every_profile_key(self):
        titles = sp.all_slot_titles(2)
        self.assertEqual(set(titles), set(sp.PROFILE_KEYS))
        self.assertTrue(all("slot2" in t for t in titles.values()))

    def test_deterministic_slot(self):
        self.assertEqual(sp.deterministic_slot(42, 3), 0)
        self.assertEqual(sp.deterministic_slot(43, 3), 1)

    def test_slot_ids_from_registry(self):
        ids = sp.slot_ids_from_registry(_REGISTRY, 1)
        self.assertEqual(ids["bhaga_model"], "s1_bhaga_model")

    def test_lease_is_stale(self):
        now = datetime.datetime(2026, 5, 30, 12, 0, tzinfo=datetime.timezone.utc)
        old = (now - datetime.timedelta(hours=2)).isoformat()
        self.assertTrue(sp._lease_is_stale(old, now, 3600))
        self.assertFalse(sp._lease_is_stale(old, now, 3 * 3600))

    def test_render_env_file(self):
        self.assertEqual(sp.render_env_file({"B": "2", "A": "1"}), "A=1\nB=2\n")


class TestProvision(unittest.TestCase):
    def test_provision_leases_clears_and_seeds(self):
        cleared: list[dict] = []
        with mock.patch.object(sp, "load_registry", lambda path=sp.POOL_REGISTRY_PATH: _REGISTRY), \
             mock.patch.object(sp, "_run_token", lambda store: ("tok", _POINTER)), \
             mock.patch.object(sp, "acquire_slot", lambda pr, n, **k: 1), \
             mock.patch.object(sp, "clear_slot", lambda t, ids: cleared.append(ids)), \
             mock.patch.object(sp, "seed_model_metadata",
                                lambda *a, **k: {"config_rows": 1, "employees_rows": 2}):
            result = sp.provision(store="palmetto", pr_number=99)
        self.assertEqual(result["slot"], 1)
        self.assertEqual(result["ids"]["bhaga_model"], "s1_bhaga_model")
        self.assertEqual(len(cleared), 1)
        self.assertIn("BHAGA_STAGING_BHAGA_MODEL_SID", result["staging_env"])


class TestTeardown(unittest.TestCase):
    def test_teardown_clears_and_releases(self):
        with mock.patch.object(sp, "load_registry", lambda path=sp.POOL_REGISTRY_PATH: _REGISTRY), \
             mock.patch.object(sp, "_run_token", lambda store: ("tok", _POINTER)), \
             mock.patch.object(sp, "_slot_held_by", lambda pr, n: 0), \
             mock.patch.object(sp, "clear_slot", mock.Mock()) as clear, \
             mock.patch.object(sp, "release_slot", mock.Mock()) as release:
            result = sp.teardown(store="palmetto", pr_number=9)
        clear.assert_called_once()
        release.assert_called_once_with(9, 0)
        self.assertTrue(result["released"])


if __name__ == "__main__":
    unittest.main()
