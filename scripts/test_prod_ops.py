"""Unit tests for scripts/prod_ops.py — parsing and the exact gcloud argv; no execution."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prod_ops import RequestError, cleanup, clone_job_spec, commands, parse_comment  # noqa: E402

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

    def test_run_job_pr_builds_copies_runs_then_deletes(self):
        req = parse_comment('/prod-ops run-job-pr bhaga-daily-refresh "BHAGA_SCHEDULE_CHANGES=open-add 2027-01-12 10:00-14:00"')
        cmds = commands(req, "436")
        image = "us-central1-docker.pkg.dev/jarvis-bhaga-prod/jarvis-images/bhaga-orchestrator:pr436-verify"
        self.assertEqual(cmds[1], ["docker", "build", "-t", image, "."])
        self.assertEqual(cmds[3][-4:], ["clone-job", "bhaga-daily-refresh", "bhaga-daily-refresh-pr436", image])
        self.assertEqual(cmds[4], ["gcloud", "run", "jobs", "execute", "bhaga-daily-refresh-pr436", *LOC, "--wait",
                                   "--update-env-vars=BHAGA_SCHEDULE_CHANGES=open-add 2027-01-12 10:00-14:00"])
        self.assertEqual(cleanup(req, "436"),
                         [["gcloud", "run", "jobs", "delete", "bhaga-daily-refresh-pr436", *LOC, "--quiet"]])
        self.assertEqual(cleanup(parse_comment("/prod-ops whoami"), "436"), [])

    def test_run_job_pr_needs_a_pr_and_a_known_job(self):
        with self.assertRaises(RequestError):
            commands(parse_comment("/prod-ops run-job-pr bhaga-daily-refresh"), None)
        with self.assertRaises(RequestError):
            parse_comment("/prod-ops run-job-pr operator-console")

    def test_clone_job_spec_renames_repoints_and_drops_system_fields(self):
        spec = {
            "apiVersion": "run.googleapis.com/v1", "kind": "Job",
            "metadata": {"name": "bhaga-daily-refresh", "uid": "u", "generation": 7,
                         "annotations": {"run.googleapis.com/creator": "x", "run.googleapis.com/launch-stage": "BETA"}},
            "spec": {"template": {
                "metadata": {"annotations": {"run.googleapis.com/client-name": "gcloud",
                                             "run.googleapis.com/execution-environment": "gen2"}},
                "spec": {"template": {"spec": {"serviceAccountName": "sa@x",
                                               "containers": [{"image": "old", "env": [{"name": "A", "value": "1"}]}]}}},
            }},
            "status": {"executionCount": 3},
        }
        out = clone_job_spec(spec, "bhaga-daily-refresh-pr436", "new")
        self.assertEqual(out["metadata"], {"name": "bhaga-daily-refresh-pr436",
                                           "annotations": {"run.googleapis.com/launch-stage": "BETA"}})
        self.assertEqual(out["spec"]["template"]["metadata"]["annotations"],
                         {"run.googleapis.com/execution-environment": "gen2"})
        inner = out["spec"]["template"]["spec"]["template"]["spec"]
        self.assertEqual((inner["serviceAccountName"], inner["containers"][0]["image"]), ("sa@x", "new"))
        self.assertEqual(inner["containers"][0]["env"], [{"name": "A", "value": "1"}])
        self.assertNotIn("status", out)

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
