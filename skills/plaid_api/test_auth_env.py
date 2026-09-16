"""Plaid environment resolution.

Regression for the nightly INVALID_API_KEYS: production keys from Secret Manager
were being sent to sandbox.plaid.com because the Cloud Run *job* — unlike both
services — never set PLAID_ENV, and the default was sandbox.
"""

from __future__ import annotations

import unittest
from unittest import mock

from skills.plaid_api import auth


class TestPlaidEnv(unittest.TestCase):
    def _env(self, **overrides) -> str:
        base = {"PLAID_ENV": "", "K_SERVICE": "", "CLOUD_RUN_JOB": ""}
        base.update(overrides)
        with mock.patch.dict("os.environ", base, clear=False):
            return auth.plaid_env()

    def test_explicit_value_always_wins(self):
        self.assertEqual(self._env(PLAID_ENV="sandbox", K_SERVICE="svc"), "sandbox")
        self.assertEqual(self._env(PLAID_ENV="production"), "production")

    def test_cloud_run_job_defaults_to_production(self):
        self.assertEqual(self._env(CLOUD_RUN_JOB="bhaga-daily-refresh"), "production")

    def test_cloud_run_service_defaults_to_production(self):
        self.assertEqual(self._env(K_SERVICE="bhaga-webhook"), "production")

    def test_off_cloud_run_stays_on_sandbox(self):
        self.assertEqual(self._env(), "sandbox")

    def test_api_base_follows_the_resolved_env(self):
        with mock.patch.object(auth, "plaid_env", return_value="production"):
            self.assertEqual(auth.api_base(), "https://production.plaid.com")
        with mock.patch.object(auth, "plaid_env", return_value="sandbox"):
            self.assertEqual(auth.api_base(), "https://sandbox.plaid.com")

    def test_unknown_env_is_rejected(self):
        with mock.patch.object(auth, "plaid_env", return_value="staging"):
            with self.assertRaises(auth.PlaidAuthError):
                auth.api_base()


if __name__ == "__main__":
    unittest.main()
