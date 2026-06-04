"""Token resolution must be cloud-native: GRAFANA_API_TOKEN env var first,
Keychain only as a local fallback, and never crash when `security` is absent
(Linux CI). Regression guard for the failed grafana-dashboard-sync deploys
where `security` did not exist on the runner (RUNBOOK §0)."""

import os
import subprocess
import unittest
from unittest.mock import patch

from skills.grafana_cloud_provisioning import provision


class TestGetApiToken(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(provision._ENV_TOKEN_VAR, None)

    def tearDown(self):
        os.environ.pop(provision._ENV_TOKEN_VAR, None)
        if self._saved is not None:
            os.environ[provision._ENV_TOKEN_VAR] = self._saved

    def test_env_var_takes_precedence_over_keychain(self):
        os.environ[provision._ENV_TOKEN_VAR] = "env-token-123"
        with patch.object(provision.subprocess, "run") as mock_run:
            token = provision.get_api_token("steadyangelfish2985")
        self.assertEqual(token, "env-token-123")
        mock_run.assert_not_called()  # Keychain never consulted

    def test_keychain_fallback_when_no_env(self):
        with patch.object(provision.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="keychain-token\n", stderr="")
            token = provision.get_api_token("steadyangelfish2985")
        self.assertEqual(token, "keychain-token")

    def test_no_security_binary_returns_none_not_crash(self):
        # Linux CI with no GRAFANA_API_TOKEN and no `security` binary.
        with patch.object(provision.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(provision.get_api_token("steadyangelfish2985"))

    def test_store_no_security_binary_is_noop(self):
        # Must not raise on a runner without `security`.
        with patch.object(provision.subprocess, "run", side_effect=FileNotFoundError):
            provision.store_api_token("tok", "steadyangelfish2985")  # no exception


if __name__ == "__main__":
    unittest.main()
