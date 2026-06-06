#!/usr/bin/env python3
"""Tests for core.datastore BQ dataset isolation (BHAGA_BQ_DATASET).

The BQ dataset is env-driven so a sandbox run writes to an isolated dataset
(`bhaga_sandbox`) instead of the prod `bhaga` dataset. These guards are the fix
for the leak that previously stranded a sandbox test row in prod BQ.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import datastore


class TestDatasetHelpers(unittest.TestCase):
    def test_dataset_defaults_to_prod(self):
        with mock.patch.object(datastore, "_DATASET", "bhaga"):
            self.assertEqual(datastore.dataset(), "bhaga")

    def test_fq_uses_active_dataset(self):
        with mock.patch.object(datastore, "_DATASET", "bhaga_sandbox"):
            self.assertEqual(
                datastore.fq("square_transactions"),
                "`jarvis-bhaga-prod.bhaga_sandbox.square_transactions`",
            )


class TestRewriteDataset(unittest.TestCase):
    def test_noop_for_default_dataset(self):
        sql = "CREATE TABLE `jarvis-bhaga-prod.bhaga.foo` (x INT64)"
        with mock.patch.object(datastore, "_DATASET", "bhaga"):
            self.assertEqual(datastore._rewrite_dataset(sql), sql)

    def test_rewrites_qualified_and_bare_refs(self):
        sql = (
            "CREATE TABLE `jarvis-bhaga-prod.bhaga.foo` (x INT64);\n"
            "INSERT INTO bhaga.bar SELECT * FROM bhaga.foo;"
        )
        with mock.patch.object(datastore, "_DATASET", "bhaga_sandbox"):
            out = datastore._rewrite_dataset(sql)
        self.assertIn("jarvis-bhaga-prod.bhaga_sandbox.foo", out)
        self.assertIn("bhaga_sandbox.bar", out)
        self.assertIn("bhaga_sandbox.foo", out)
        # The project id (jarvis-bhaga-prod) must NOT be mangled.
        self.assertIn("jarvis-bhaga-prod.bhaga_sandbox.foo", out)
        self.assertNotIn("jarvis-bhaga_sandbox-prod", out)


class TestSandboxWriteIsolation(unittest.TestCase):
    def test_allows_non_staging_run_to_prod(self):
        with mock.patch.dict(os.environ, {"BHAGA_SHEET_MODE": "prod"}, clear=False):
            with mock.patch.object(datastore, "_DATASET", "bhaga"):
                datastore._assert_sandbox_write_isolation()  # no raise

    def test_blocks_staging_run_writing_prod_dataset(self):
        with mock.patch.dict(os.environ, {"BHAGA_SHEET_MODE": "staging"}, clear=False):
            with mock.patch.object(datastore, "_DATASET", "bhaga"):
                with self.assertRaises(RuntimeError):
                    datastore._assert_sandbox_write_isolation()

    def test_allows_staging_run_writing_sandbox_dataset(self):
        with mock.patch.dict(os.environ, {"BHAGA_SHEET_MODE": "staging"}, clear=False):
            with mock.patch.object(datastore, "_DATASET", "bhaga_sandbox"):
                datastore._assert_sandbox_write_isolation()  # no raise


if __name__ == "__main__":
    unittest.main()
