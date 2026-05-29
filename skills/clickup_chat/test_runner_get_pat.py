#!/usr/bin/env python3
"""Unit tests for skills.clickup_chat.runner.get_pat — cloud vs local resolution.

Run:
    python3 skills/clickup_chat/test_runner_get_pat.py

Covers the Cloud Run fix for the nightly review_fetch step: in the Linux
Cloud Run container the macOS `security` binary is absent, so get_pat() must
read the PAT from the secret-backed CLICKUP_PAT env var. Locally it falls back
to the macOS Keychain. A missing env var in cloud raises a clear error (not
FileNotFoundError('security')).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.clickup_chat import runner


_CLOUD_ENV_KEYS = ("BHAGA_SECRETS_BACKEND", "K_SERVICE", "CLOUD_RUN_JOB", "CLICKUP_PAT")


def _clean_env(**overrides):
    """Return an os.environ dict with cloud/PAT keys cleared, plus overrides."""
    env = {k: v for k, v in os.environ.items() if k not in _CLOUD_ENV_KEYS}
    env.update(overrides)
    return env


class GetPatCloudPathTests(unittest.TestCase):
    def test_cloud_reads_env_var(self):
        """Cloud Run: PAT comes from CLICKUP_PAT env, no `security` shellout."""
        env = _clean_env(BHAGA_SECRETS_BACKEND="gcp", CLICKUP_PAT="pk_cloud_token_123")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(runner.subprocess, "run") as mock_run:
            self.assertEqual(runner.get_pat(), "pk_cloud_token_123")
            mock_run.assert_not_called()

    def test_cloud_env_var_via_k_service(self):
        """K_SERVICE alone (with env PAT) is enough — env wins regardless."""
        env = _clean_env(K_SERVICE="bhaga-daily-refresh", CLICKUP_PAT="pk_via_kservice")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(runner.subprocess, "run") as mock_run:
            self.assertEqual(runner.get_pat(), "pk_via_kservice")
            mock_run.assert_not_called()

    def test_cloud_missing_env_raises_clear_error(self):
        """Cloud Run with no CLICKUP_PAT: clear RuntimeError, no `security` call."""
        env = _clean_env(CLOUD_RUN_JOB="bhaga-daily-refresh")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(runner.subprocess, "run") as mock_run:
            with self.assertRaises(RuntimeError) as ctx:
                runner.get_pat()
            mock_run.assert_not_called()
            msg = str(ctx.exception)
            self.assertIn("CLICKUP_PAT", msg)
            self.assertIn("Cloud Run", msg)

    def test_cloud_env_var_bad_prefix_raises(self):
        """A non-'pk_' env value is rejected with a validation error."""
        env = _clean_env(BHAGA_SECRETS_BACKEND="gcp", CLICKUP_PAT="garbage_value")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                runner.get_pat()
            self.assertIn("pk_", str(ctx.exception))


class GetPatLocalPathTests(unittest.TestCase):
    def test_local_uses_keychain(self):
        """Local (no cloud env, no CLICKUP_PAT): reads from macOS Keychain."""
        env = _clean_env()
        fake = mock.Mock(returncode=0, stdout="pk_local_keychain_token\n", stderr="")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(runner.subprocess, "run", return_value=fake) as mock_run:
            self.assertEqual(runner.get_pat(), "pk_local_keychain_token")
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertEqual(args[0], "security")
            self.assertIn(runner.KEYCHAIN_SERVICE, args)

    def test_local_env_var_takes_precedence(self):
        """Even locally, an explicit CLICKUP_PAT env var wins over keychain."""
        env = _clean_env(CLICKUP_PAT="pk_explicit_local")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(runner.subprocess, "run") as mock_run:
            self.assertEqual(runner.get_pat(), "pk_explicit_local")
            mock_run.assert_not_called()

    def test_local_keychain_failure_raises(self):
        """Local keychain miss raises the registry-setup hint."""
        env = _clean_env()
        fake = mock.Mock(returncode=44, stdout="", stderr="not found")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(runner.subprocess, "run", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                runner.get_pat()
            self.assertIn("Keychain", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
