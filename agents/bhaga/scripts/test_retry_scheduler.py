#!/usr/bin/env python3
"""Tests for the one-shot Cloud Scheduler smart-retry helper.

Pure spec-building (cron, target URI, env overrides, tz conversion) is tested
directly; create/delete are tested against a fake CloudSchedulerClient so no GCP
call is made.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import retry_scheduler as rs  # noqa: E402

UTC = datetime.timezone.utc


class _FakeClient:
    def __init__(self, *, exists=False):
        self.created = []
        self.deleted = []
        self._exists = exists

    def delete_job(self, name):
        self.deleted.append(name)
        if not self._exists:
            raise RuntimeError("404 not found")

    def create_job(self, parent, job):
        self.created.append((parent, job))
        return job


class TestPureBuild(unittest.TestCase):
    def test_cron_components_order(self):
        local = datetime.datetime(2026, 6, 29, 1, 7)  # 01:07 CT → "M H D Mo *"
        self.assertEqual(rs.cron_for(local), "7 1 29 6 *")

    def test_build_job_converts_utc_to_ct(self):
        # 06:07 UTC == 01:07 CDT (America/Chicago, UTC-5 in summer)
        retry_at = datetime.datetime(2026, 6, 29, 6, 7, tzinfo=UTC)
        job = rs.build_retry_job(
            "2026-06-28", retry_at,
            env={"REFRESH_DATE": "2026-06-28", "BHAGA_MAINT_RETRY_ATTEMPT": "1"},
        )
        # business date is the 28th, but 06:07 UTC == 01:07 CDT on the 29th → fire day 29
        self.assertEqual(job["schedule"], "7 1 29 6 *")
        self.assertEqual(job["time_zone"], "America/Chicago")
        self.assertEqual(job["name"],
                         f"{rs.parent_path()}/jobs/bhaga-retry-2026-06-28")

    def test_build_job_target_and_env(self):
        retry_at = datetime.datetime(2026, 6, 29, 6, 7, tzinfo=UTC)
        job = rs.build_retry_job("2026-06-28", retry_at,
                                 env={"REFRESH_DATE": "2026-06-28"})
        ht = job["http_target"]
        self.assertIn(":run", ht["uri"])
        self.assertIn("run.googleapis.com/apis/run.googleapis.com/v1/namespaces", ht["uri"])
        self.assertEqual(ht["http_method"], "POST")
        self.assertEqual(ht["oauth_token"]["service_account_email"], rs.INVOKER_SA)
        body = json.loads(ht["body"].decode("utf-8"))
        env = body["overrides"]["containerOverrides"][0]["env"]
        self.assertIn({"name": "REFRESH_DATE", "value": "2026-06-28"}, env)

    def test_build_job_winter_offset(self):
        # 07:07 UTC in January == 01:07 CST (UTC-6)
        retry_at = datetime.datetime(2026, 1, 6, 7, 7, tzinfo=UTC)
        job = rs.build_retry_job("2026-01-05", retry_at, env={})
        self.assertEqual(job["schedule"], "7 1 6 1 *")


class TestScheduleAndDelete(unittest.TestCase):
    def test_schedule_deletes_then_creates(self):
        fake = _FakeClient(exists=False)
        retry_at = datetime.datetime(2026, 6, 29, 6, 7, tzinfo=UTC)
        rs.schedule_one_shot_retry("2026-06-28", retry_at,
                                   env={"REFRESH_DATE": "2026-06-28"}, client=fake)
        # delete attempted first (idempotent), then create
        self.assertEqual(len(fake.deleted), 1)
        self.assertEqual(len(fake.created), 1)
        parent, job = fake.created[0]
        self.assertEqual(parent, rs.parent_path())
        self.assertTrue(job["name"].endswith("bhaga-retry-2026-06-28"))

    def test_delete_returns_true_when_present(self):
        fake = _FakeClient(exists=True)
        self.assertTrue(rs.delete_retry_schedule("2026-06-28", client=fake))
        self.assertEqual(fake.deleted, [rs.job_path("2026-06-28")])

    def test_delete_returns_false_when_absent(self):
        fake = _FakeClient(exists=False)
        self.assertFalse(rs.delete_retry_schedule("2026-06-28", client=fake))


if __name__ == "__main__":
    unittest.main()
