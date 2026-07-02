"""Regression test for the 2026-07-01 incident (Issue #126).

commit 1af7608d added an `ingest_inventory` nightly step whose env dict read
`run_id` directly from `_run_refresh()`'s scope, but `run_id` is a local
variable of the separate `main()` function — not a nested closure — so every
nightly crashed with `NameError: name 'run_id' is not defined` the instant it
reached this step. Because the exception was unhandled, it aborted the
ENTIRE nightly run (not just inventory), which is why Order Assistant (and
everything scheduled after it) went stale starting the night of 7/1.

The fix threads `run_id` explicitly: `main()` passes it into `_run_refresh(run_id)`,
which passes it into `_build_ingest_inventory_env(run_id)`. This test locks
both the standalone helper's behavior and the call-chain wiring so the bug
class (a variable silently relying on the wrong function's scope) can't
regress silently.
"""
from __future__ import annotations

import inspect
import os
import unittest

import agents.bhaga.scripts.daily_refresh as dr


class TestBuildIngestInventoryEnv(unittest.TestCase):
    def test_threads_run_id_into_env(self):
        env = dr._build_ingest_inventory_env("abc123")
        self.assertEqual(env["BHAGA_RUN_ID"], "abc123")
        self.assertEqual(env["BHAGA_DATASTORE"], "bigquery")
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_inherits_parent_environment(self):
        os.environ["_TEST_INGEST_ENV_MARKER"] = "present"
        try:
            env = dr._build_ingest_inventory_env("xyz")
            self.assertEqual(env["_TEST_INGEST_ENV_MARKER"], "present")
        finally:
            del os.environ["_TEST_INGEST_ENV_MARKER"]

    def test_no_nameerror_with_only_run_id_argument(self):
        """The exact failure mode: calling with just run_id must not raise."""
        try:
            dr._build_ingest_inventory_env(run_id="deadbeef")
        except NameError as exc:  # pragma: no cover - the bug we're guarding against
            self.fail(f"_build_ingest_inventory_env raised NameError: {exc}")


class TestRunRefreshReceivesRunId(unittest.TestCase):
    """`_run_refresh` must accept run_id as an explicit parameter, never an
    implicit reference to a variable that only exists in main()'s scope."""

    def test_run_refresh_signature_requires_run_id(self):
        sig = inspect.signature(dr._run_refresh)
        self.assertIn("run_id", sig.parameters)

    def test_main_passes_its_run_id_to_run_refresh(self):
        source = inspect.getsource(dr.main)
        self.assertIn("_run_refresh(run_id)", source)


if __name__ == "__main__":
    unittest.main()
