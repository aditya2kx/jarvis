#!/usr/bin/env python3
"""Tests for ADP trusted-device session reuse (Issue #305).

`gcs_cache.upload_session` / `download_session` and `launch_persistent`'s
`storage_state` parameter all existed but had ZERO callers, so every ADP login
started from a fresh cookie jar and was fully exposed whenever ADP's risk engine
challenged. These cover the wiring that makes the next run a trusted device.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.adp_run_automation import runner


class TestRestoreAdpSession(unittest.TestCase):
    def test_disabled_without_the_env_flag(self):
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": ""}):
            self.assertIsNone(runner._restore_adp_session(store="palmetto"))

    def test_returns_local_path_on_a_cache_hit(self):
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.download_session",
                        return_value=True):
            self.assertEqual(
                runner._restore_adp_session(store="palmetto"),
                str(runner.ADP_SESSION_LOCAL),
            )

    def test_returns_none_on_a_cache_miss(self):
        """A miss must mean a fresh jar and a full login, never a bogus path —
        launch_persistent would otherwise be handed a nonexistent file."""
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.download_session",
                        return_value=False):
            self.assertIsNone(runner._restore_adp_session(store="palmetto"))


class TestPersistAdpSession(unittest.TestCase):
    def test_saves_then_uploads(self):
        ctx = mock.Mock()
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session") as up:
            runner._persist_adp_session(ctx, store="palmetto")
        ctx.storage_state.assert_called_once_with(
            path=str(runner.ADP_SESSION_LOCAL),
        )
        up.assert_called_once()
        self.assertEqual(up.call_args.kwargs["portal"], "adp")
        self.assertEqual(up.call_args.kwargs["store"], "palmetto")

    def test_disabled_without_the_env_flag(self):
        ctx = mock.Mock()
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": ""}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session") as up:
            runner._persist_adp_session(ctx, store="palmetto")
        ctx.storage_state.assert_not_called()
        up.assert_not_called()

    def test_storage_state_failure_never_fails_the_run(self):
        """The scrape's data has already landed by this point; losing the
        session costs one extra login, not the run."""
        ctx = mock.Mock()
        ctx.storage_state.side_effect = RuntimeError("context closed")
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session"):
            runner._persist_adp_session(ctx, store="palmetto")

    def test_upload_failure_never_fails_the_run(self):
        ctx = mock.Mock()
        with mock.patch.dict(os.environ, {"BHAGA_SESSION_PERSIST": "1"}), \
             mock.patch("agents.bhaga.scripts.gcs_cache.upload_session",
                        side_effect=RuntimeError("GCS down")):
            runner._persist_adp_session(ctx, store="palmetto")


class TestBundleWiring(unittest.TestCase):
    """The bundle session is the nightly's single ADP login.

    Guards against the wiring silently reverting to the state this issue found:
    the parameter and the helpers existing with nothing connecting them.
    """

    def test_bundle_passes_storage_state_and_saves_after_login(self):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner.py")
        with open(src) as fh:
            text = fh.read()
        bundle = text.split("def download_adp_bundle", 1)[1]
        self.assertIn("storage_state=_restore_adp_session(store=store)", bundle)
        # Saved right after login, not at block exit: a later component can
        # fail and the next run should still get a trusted device.
        login = bundle.index("_ensure_logged_in(page, store=store)")
        persist = bundle.index("_persist_adp_session(ctx, store=store)")
        self.assertLess(login, persist)


if __name__ == "__main__":
    unittest.main()
