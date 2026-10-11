"""Unit tests for scripts/prod_ops.py — parsing and the exact gcloud argv; no execution."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prod_ops import RequestError, commands, parse_comment  # noqa: E402

LOC = ["--region=us-central1", "--project=jarvis-bhaga-prod"]


class ProdOpsTests(unittest.TestCase):
    def test_whoami_is_read_only(self):
        self.assertEqual(commands(parse_comment("/prod-ops whoami"))[0][:3], ["gcloud", "auth", "list"])

    def test_console_env_set_and_unset(self):
        set_ = commands(parse_comment("/prod-ops console-env set CONSOLE_ADP_SCHEDULE_WRITE=1 FOO=bar"))
        self.assertEqual(set_, [["gcloud", "run", "services", "update", "operator-console", *LOC,
                                 "--update-env-vars=CONSOLE_ADP_SCHEDULE_WRITE=1,FOO=bar", "--quiet"]])
        unset = commands(parse_comment("/prod-ops console-env unset FOO"))
        self.assertIn("--remove-env-vars=FOO", unset[0])

    def test_deploy_owned_keys_are_refused(self):
        for body in ("/prod-ops console-env set CONSOLE_PREVIEW=1", "/prod-ops console-env unset BQ_DATASET"):
            with self.assertRaises(RequestError, msg=body):
                parse_comment(body)

    def test_run_job_with_env(self):
        cmd = commands(parse_comment("/prod-ops run-job bhaga-daily-refresh REFRESH_DATE=2026-10-10"))[0]
        self.assertEqual(cmd[:5], ["gcloud", "run", "jobs", "execute", "bhaga-daily-refresh"])
        self.assertIn("--async", cmd)
        self.assertEqual(cmd[-1], "--update-env-vars=REFRESH_DATE=2026-10-10")

    def test_rejects_injection_and_junk(self):
        for body in (
            "/prod-ops run-job 'bhaga; rm -rf /'",
            "/prod-ops console-env set lower=1",
            "/prod-ops console-env set A=1,B=2",
            "/prod-ops apply-migrations now",
            "/prod-ops delete-everything",
            "/prod-read status",
        ):
            with self.assertRaises(RequestError, msg=body):
                parse_comment(body)


if __name__ == "__main__":
    unittest.main()
