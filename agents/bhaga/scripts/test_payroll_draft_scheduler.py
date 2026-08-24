#!/usr/bin/env python3
"""Pure spec tests for bhaga-payroll-draft Cloud Scheduler."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import payroll_draft_scheduler as pds  # noqa: E402
from agents.bhaga.scripts import retry_scheduler as rs  # noqa: E402


class TestPayrollDraftSchedulerSpec(unittest.TestCase):
    def test_monday_7am_chicago(self):
        job = pds.build_payroll_draft_job()
        self.assertEqual(job["schedule"], "0 7 * * 1")
        self.assertEqual(job["time_zone"], "America/Chicago")
        self.assertTrue(job["name"].endswith("/jobs/bhaga-payroll-draft"))

    def test_targets_daily_refresh_with_draft_only_env(self):
        job = pds.build_payroll_draft_job(store="palmetto")
        ht = job["http_target"]
        self.assertIn("jobs/bhaga-daily-refresh:run", ht["uri"])
        self.assertEqual(ht["http_method"], "POST")
        self.assertEqual(ht["oauth_token"]["service_account_email"], rs.INVOKER_SA)
        body = json.loads(ht["body"].decode("utf-8"))
        env = {e["name"]: e["value"] for e in body["overrides"]["containerOverrides"][0]["env"]}
        self.assertEqual(env["BHAGA_PAYROLL_DRAFT_ONLY"], "1")
        self.assertEqual(env["BHAGA_ADP_PAYROLL_DRAFT"], "1")
        self.assertEqual(env["BHAGA_IGNORE_HALT"], "1")
        self.assertEqual(env["BHAGA_STORE"], "palmetto")
        self.assertNotIn("BHAGA_SKIP_ADP", env)


if __name__ == "__main__":
    unittest.main()
