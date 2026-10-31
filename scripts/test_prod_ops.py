"""Unit tests for scripts/prod_ops.py — parsing and the exact gcloud argv; no execution."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prod_ops  # noqa: E402
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

    def _run_main(self, fail_on: str | None, body: str = "/prod-ops run-job-pr bhaga-daily-refresh A=1",
):
        ran = []

        def fake_run(cmd, **_kw):
            ran.append(cmd)
            return mock.Mock(returncode=1 if fail_on and fail_on in cmd else 0)

        env = {"COMMENT_BODY": body, "ISSUE": "436", "HEAD_SHA": "abc", "PROD_OPS_CODE_ROOT": "/tmp"}
        with mock.patch.dict(os.environ, env), mock.patch("prod_ops.subprocess.run", fake_run):
            rc = prod_ops.main(["run"])
        steps = {"unittest", "configure-docker", "build", "push", "clone-job", "execute", "delete", "list", "read"}
        return rc, [next(a for a in c if a in steps) for c in ran]

    def test_run_deletes_the_copy_even_when_its_execution_fails(self):
        # A=1 is changes-prod: it runs with no approval step.
        self.assertEqual(self._run_main(None), (0, ["configure-docker", "build", "push", "clone-job", "execute", "delete"]))
        # On execute failure, dump_failed_job lists the execution (and tries logging read) before delete.
        self.assertEqual(self._run_main("execute"),
                         (1, ["configure-docker", "build", "push", "clone-job", "execute", "list", "read", "delete"]))

    def test_run_skips_the_delete_when_no_copy_was_started(self):
        self.assertEqual(self._run_main("build"), (1, ["configure-docker", "build"]))

    def test_safe_dry_run_runs_its_no_write_tests_first_and_stops_if_they_fail(self):
        body = "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_SCHEDULE_WRITE=apply BHAGA_SCHEDULE_DRY_RUN=1"
        self.assertEqual(self._run_main(None, body),
                         (0, ["unittest", "configure-docker", "build", "push", "clone-job", "execute", "delete"]))
        self.assertEqual(self._run_main("unittest", body), (1, ["unittest"]))

    def test_read_sync_runs_without_the_schedule_tests(self):
        body = "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_HIRE_DATES=1"
        self.assertEqual(self._run_main(None, body),
                         (0, ["configure-docker", "build", "push", "clone-job", "execute", "delete"]))


class TierTests(unittest.TestCase):
    """Which ops the PR reply labels safe vs changes-prod."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "core" / "migrations").mkdir(parents=True)

    def _tier(self, body: str) -> str:
        return prod_ops.tier(parse_comment(body), self.root)[0]

    def _migration(self, name: str, sql: str) -> None:
        (self.root / "core" / "migrations" / name).write_text(sql)

    def test_only_read_only_and_dry_runs_are_safe(self):
        dry = "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_SCHEDULE_WRITE=apply BHAGA_SCHEDULE_DRY_RUN=1"
        self.assertEqual(self._tier("/prod-ops whoami"), "safe")
        self.assertEqual(self._tier(dry + ' BHAGA_STORE=palmetto "BHAGA_SCHEDULE_CHANGES=open-add 2026-11-03 10:00-14:00"'),
                         "safe")
        for body in (
            "/prod-ops console-env set CONSOLE_X=1",
            "/prod-ops run-job bhaga-daily-refresh BHAGA_SCHEDULE_DRY_RUN=1",
            "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_SCHEDULE_WRITE=apply",
            "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_SCHEDULE_WRITE=apply BHAGA_SCHEDULE_DRY_RUN=0",
            "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_SCHEDULE_WRITE=publish BHAGA_SCHEDULE_DRY_RUN=1",
            dry + " REFRESH_DATE=2026-10-10",
        ):
            self.assertEqual(self._tier(body), "changes-prod", body)

    def test_a_single_adp_read_sync_is_safe(self):
        for body in (
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_HIRE_DATES=1",
            "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_HIRE_DATES=1 BHAGA_STORE=palmetto",
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_SYNC_ALL=1",
            "/prod-ops run-job-pr bhaga-daily-refresh BHAGA_ADP_SCHEDULE_ONLY=1",
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_TIMECARD_ONLY=1",
        ):
            self.assertEqual(self._tier(body), "safe", body)
        for body in (
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_HIRE_DATES=0",
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_HIRE_DATES=1 BHAGA_ADP_SYNC_ALL=1",
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_SYNC_ALL=1 BHAGA_PAYROLL_DRAFT_ONLY=1",
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_TIMECARD_ONLY=1 BHAGA_PUNCH_FIX_APPLY_ONLY=1",
            "/prod-ops run-job bhaga-daily-refresh BHAGA_ADP_SYNC_ALL=1 REFRESH_DATE=2026-10-10",
            "/prod-ops run-job other-job BHAGA_ADP_SYNC_ALL=1",
            "/prod-ops run-job bhaga-daily-refresh",
        ):
            self.assertEqual(self._tier(body), "changes-prod", body)

    def test_a_migrations_own_test_file_is_not_a_runner_change(self):
        self._migration("900_new.sql", "CREATE TABLE IF NOT EXISTS `p.d.t` (a INT64);")
        (self.root / "core" / "test_migration_900_new.py").write_text("import unittest\n")
        self.assertEqual(self._tier("/prod-ops apply-migrations"), "safe")

    def test_additive_migrations_are_safe(self):
        self._migration("900_new.sql", "-- never UPDATE/DELETE/MERGE this table\n"
                        "CREATE TABLE IF NOT EXISTS `p.d.t` (a INT64);\n"
                        "ALTER TABLE `p.d.t` ADD COLUMN IF NOT EXISTS b STRING;\n"
                        "CREATE OR REPLACE VIEW `p.d.v` AS SELECT a FROM `p.d.t`;\n"
                        "INSERT INTO `p.d.t` (a) VALUES (1);")
        self.assertEqual(self._tier("/prod-ops apply-migrations"), "safe")

    def test_migrations_that_can_lose_or_rewrite_rows_change_prod(self):
        for sql in ("DROP TABLE `p.d.t`;", "ALTER TABLE `p.d.t` DROP COLUMN b;", "DELETE FROM `p.d.t` WHERE TRUE;",
                    "UPDATE `p.d.t` SET a = 1 WHERE TRUE;", "MERGE `p.d.t` T USING s ON FALSE WHEN NOT MATCHED THEN INSERT ROW;",
                    "TRUNCATE TABLE `p.d.t`;", "CREATE OR REPLACE TABLE `p.d.t` (a INT64);",
                    "ALTER TABLE `p.d.t` SET OPTIONS (partition_expiration_days = 7);",
                    "EXECUTE IMMEDIATE CONCAT('DR', 'OP TABLE `p.d.t`');",
                    "CREATE TABLE IF NOT EXISTS `p.d.t` (a INT64); -- then DROP it"):
            self._migration("901_risky.sql", sql)
            self.assertEqual(self._tier("/prod-ops apply-migrations"), "changes-prod", sql)

    def test_a_changed_migration_runner_changes_prod(self):
        self._migration("900_new.sql", "CREATE TABLE IF NOT EXISTS `p.d.t` (a INT64);")
        runner = self.root / "core" / "datastore.py"
        runner.write_bytes((prod_ops.REPO_ROOT / "core" / "datastore.py").read_bytes())
        self.assertEqual(self._tier("/prod-ops apply-migrations"), "safe")
        runner.write_text(runner.read_text() + "\n# runs other SQL\n")
        self.assertEqual(prod_ops.tier(parse_comment("/prod-ops apply-migrations"), self.root),
                         ("changes-prod", "migration runner differs from main: core/datastore.py"))

    def test_migrations_already_on_main_are_not_rechecked(self):
        main_copy = next((prod_ops.REPO_ROOT / "core" / "migrations").glob("*.sql"))
        self._migration(main_copy.name, main_copy.read_text() + "\nDROP TABLE `p.d.t`;")
        self.assertEqual(prod_ops.new_migrations(self.root)[0].name, main_copy.name)
        (self.root / "core" / "migrations" / main_copy.name).write_bytes(main_copy.read_bytes())
        self.assertEqual(prod_ops.new_migrations(self.root), [])

    def test_plan_reports_the_tier_and_why(self):
        env = {"COMMENT_BODY": "/prod-ops whoami", "PROD_OPS_CODE_ROOT": str(self.root)}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            prod_ops.main(["plan"])
        plan = json.loads(out.getvalue())
        self.assertEqual((plan["tier"], plan["why"]), ("safe", "read-only"))

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
