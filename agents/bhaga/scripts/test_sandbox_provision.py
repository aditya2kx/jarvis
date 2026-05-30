#!/usr/bin/env python3
"""Offline tests for sandbox_provision — no Google API calls.

All Google Sheets/Drive I/O is monkeypatched so the orchestration logic,
PR-scoped naming, env emission, and idempotent reuse are exercised without
credentials. Live provisioning is verified by the CI e2e job (WIF).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import sandbox_provision as sp


class TestPureHelpers(unittest.TestCase):
    def test_staging_env_key(self):
        self.assertEqual(sp.staging_env_key("bhaga_model"), "BHAGA_STAGING_BHAGA_MODEL_SID")
        self.assertEqual(sp.staging_env_key("bhaga_adp_raw"), "BHAGA_STAGING_BHAGA_ADP_RAW_SID")

    def test_sandbox_title_is_pr_scoped_and_deterministic(self):
        self.assertEqual(sp.sandbox_title(42, "bhaga_model"), "BHAGA-sandbox PR#42 bhaga_model")
        self.assertEqual(sp.sandbox_title(42, "bhaga_model"), sp.sandbox_title(42, "bhaga_model"))
        self.assertNotEqual(sp.sandbox_title(42, "bhaga_model"), sp.sandbox_title(43, "bhaga_model"))

    def test_all_sandbox_titles_covers_every_profile_key(self):
        titles = sp.all_sandbox_titles(7)
        self.assertEqual(set(titles), set(sp.PROFILE_KEYS))
        self.assertTrue(all("PR#7" in t for t in titles.values()))

    def test_staging_env_maps_all_keys(self):
        ids = {k: f"id_{k}" for k in sp.PROFILE_KEYS}
        env = sp.staging_env(ids)
        self.assertEqual(env["BHAGA_STAGING_BHAGA_MODEL_SID"], "id_bhaga_model")
        self.assertEqual(len(env), len(sp.PROFILE_KEYS))

    def test_render_env_file_is_sorted_keyvalue_lines(self):
        text = sp.render_env_file({"B": "2", "A": "1"})
        self.assertEqual(text, "A=1\nB=2\n")

    def test_tab_specs_for_each_key(self):
        self.assertEqual([t["tab_name"] for t in sp._tab_specs_for("bhaga_model")], ["config", "employees"])
        self.assertIn("transactions", [t["tab_name"] for t in sp._tab_specs_for("bhaga_square_raw")])
        self.assertIn("shifts", [t["tab_name"] for t in sp._tab_specs_for("bhaga_adp_raw")])
        self.assertEqual([t["tab_name"] for t in sp._tab_specs_for("bhaga_review_raw")], ["reviews", "config"])

    def test_tab_specs_for_unknown_raises(self):
        with self.assertRaises(KeyError):
            sp._tab_specs_for("nope")

    def test_col_a1(self):
        self.assertEqual(sp._col_a1(1), "A")
        self.assertEqual(sp._col_a1(26), "Z")
        self.assertEqual(sp._col_a1(27), "AA")
        self.assertEqual(sp._col_a1(0), "A")  # clamps to >=1


_POINTER = {
    "google_account_key": "palmetto",
    "google_sheets": {"bhaga_model": {"spreadsheet_id": "PROD_MODEL"}},
}


class TestSeedModelMetadata(unittest.TestCase):
    def test_copies_config_and_employees_and_counts_data_rows(self):
        reads = {
            "config!A1:F200": [["key", "value", "notes"], ["store", "Palmetto", ""], ["shop_open", "10:00", ""]],
            "employees!A1:E500": [["canonical_name", "aliases", "notes"], ["Krause, Lindsay", "Lindsay", "mgr"]],
        }
        writes: list[tuple] = []

        def fake_read(token, sid, rng):
            self.assertEqual(sid, "PROD_MODEL")
            return reads[rng]

        def fake_write(token, sid, rng, values):
            self.assertEqual(sid, "SANDBOX_MODEL")
            writes.append((rng, values))

        with mock.patch.object(sp, "_read_values", fake_read), \
             mock.patch.object(sp, "_write_values", fake_write):
            counts = sp.seed_model_metadata(
                "tok", prod_model_sid="PROD_MODEL", sandbox_model_sid="SANDBOX_MODEL"
            )
        self.assertEqual(counts, {"config_rows": 2, "employees_rows": 1})
        self.assertEqual(len(writes), 2)
        # config has 3 columns -> A1:C3
        self.assertEqual(writes[0][0], "config!A1:C3")

    def test_empty_prod_tab_writes_nothing(self):
        with mock.patch.object(sp, "_read_values", lambda *a: []), \
             mock.patch.object(sp, "_write_values", mock.Mock()) as w:
            counts = sp.seed_model_metadata("t", prod_model_sid="P", sandbox_model_sid="S")
        self.assertEqual(counts, {"config_rows": 0, "employees_rows": 0})
        w.assert_not_called()


class TestProvision(unittest.TestCase):
    def _patches(self, existing=None):
        existing = existing or {}
        created: list[str] = []

        def fake_find_folder(token, name):
            return "FOLDER" if name == sp.SANDBOX_FOLDER_NAME else None

        def fake_find_sheet(token, folder, title):
            return existing.get(title)

        def fake_create(token, title, specs):
            sid = f"NEW_{title.split()[-1]}"
            created.append(sid)
            return {"spreadsheetId": sid}

        return created, [
            mock.patch.object(sp, "refresh_access_token", lambda account=None: "tok"),
            mock.patch.object(sp, "_load_pointer", lambda store: _POINTER),
            mock.patch.object(sp, "find_folder_at_root", fake_find_folder),
            mock.patch.object(sp, "find_spreadsheet_in_folder", fake_find_sheet),
            mock.patch.object(sp, "create_spreadsheet", fake_create),
            mock.patch.object(sp, "move_file_into_folder", mock.Mock()),
            mock.patch.object(sp, "seed_tab_headers", mock.Mock()),
            mock.patch.object(sp, "seed_model_metadata", lambda *a, **k: {"config_rows": 5, "employees_rows": 12}),
            mock.patch.object(sp, "create_folder_at_root", lambda t, n: "FOLDER"),
        ]

    def test_provision_creates_four_sheets_and_env(self):
        created, patches = self._patches()
        for p in patches:
            p.start()
        try:
            result = sp.provision(store="palmetto", pr_number=42)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(len(result["ids"]), 4)
        self.assertEqual(len(created), 4)
        self.assertIn("BHAGA_STAGING_BHAGA_MODEL_SID", result["staging_env"])
        self.assertEqual(result["pr_number"], 42)

    def test_provision_is_idempotent_reuses_existing(self):
        existing = {sp.sandbox_title(42, "bhaga_model"): "EXISTING_MODEL"}
        created, patches = self._patches(existing=existing)
        for p in patches:
            p.start()
        try:
            result = sp.provision(store="palmetto", pr_number=42)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(result["ids"]["bhaga_model"], "EXISTING_MODEL")
        # only 3 newly created (model reused)
        self.assertEqual(len(created), 3)


class TestTeardown(unittest.TestCase):
    def test_teardown_deletes_found_sheets(self):
        deleted: list[str] = []
        title_map = {sp.sandbox_title(9, "bhaga_model"): "M", sp.sandbox_title(9, "bhaga_adp_raw"): "A"}
        with mock.patch.object(sp, "refresh_access_token", lambda account=None: "tok"), \
             mock.patch.object(sp, "_load_pointer", lambda store: _POINTER), \
             mock.patch.object(sp, "find_folder_at_root", lambda t, n: "FOLDER"), \
             mock.patch.object(sp, "find_spreadsheet_in_folder", lambda t, f, title: title_map.get(title)), \
             mock.patch.object(sp, "_delete_file", lambda t, fid: deleted.append(fid)):
            result = sp.teardown(store="palmetto", pr_number=9)
        self.assertEqual(set(result["deleted"]), {"M", "A"})
        self.assertEqual(set(deleted), {"M", "A"})

    def test_teardown_no_folder_is_noop(self):
        with mock.patch.object(sp, "refresh_access_token", lambda account=None: "tok"), \
             mock.patch.object(sp, "_load_pointer", lambda store: _POINTER), \
             mock.patch.object(sp, "find_folder_at_root", lambda t, n: None):
            result = sp.teardown(store="palmetto", pr_number=9)
        self.assertEqual(result["deleted"], [])


class TestMainCli(unittest.TestCase):
    def test_main_provision_writes_env_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w+", suffix=".env", delete=False) as tf:
            env_path = tf.name
        try:
            fake_result = {
                "pr_number": 1,
                "ids": {k: f"id_{k}" for k in sp.PROFILE_KEYS},
                "staging_env": sp.staging_env({k: f"id_{k}" for k in sp.PROFILE_KEYS}),
                "seed_counts": {},
                "folder_id": "F",
            }
            with mock.patch.object(sp, "provision", lambda **k: fake_result):
                rc = sp.main(["--pr-number", "1", "--action", "provision", "--emit-env-file", env_path])
            self.assertEqual(rc, 0)
            with open(env_path) as f:
                content = f.read()
            self.assertIn("BHAGA_STAGING_BHAGA_MODEL_SID=id_bhaga_model", content)
        finally:
            os.unlink(env_path)

    def test_main_teardown(self):
        with mock.patch.object(sp, "teardown", lambda **k: {"pr_number": 5, "deleted": ["x"]}):
            rc = sp.main(["--pr-number", "5", "--action", "teardown"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
