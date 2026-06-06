"""Tests for sandbox_live_run pure helpers — env construction + the isolation
pre-flight that must fail loud before any deploy/execute touches prod."""

from __future__ import annotations

import pytest

from agents.bhaga.scripts import sandbox_live_run as slr


def _good_ids() -> dict[str, str]:
    return {
        "bhaga_model": "SID_MODEL",
        "bhaga_adp_raw": "SID_ADP",
        "bhaga_square_raw": "SID_SQUARE",
        "bhaga_review_raw": "SID_REVIEW",
    }


def _good_env() -> dict:
    return slr.build_sandbox_env(
        staging_ids=_good_ids(),
        refresh_date="2026-05-31",
        store="palmetto",
        run_label="PR#42 fix/item-sales",
    )


class TestBuildSandboxEnv:
    def test_sets_isolation_overrides(self):
        env = _good_env()
        assert env["BHAGA_SHEET_MODE"] == "staging"
        assert env["BHAGA_GCS_CACHE_WRITE_BUCKET"] == slr.SANDBOX_CACHE_WRITE_BUCKET
        assert env["BHAGA_FIRESTORE_COLLECTION"] == slr.SANDBOX_RUNS_COLLECTION
        assert env["BHAGA_BQ_DATASET"] == slr.SANDBOX_BQ_DATASET
        assert env["REFRESH_DATE"] == "2026-05-31"

    def test_bq_dataset_is_isolated(self):
        # Sandbox writes must land in an isolated dataset, never prod `bhaga`.
        assert _good_env()["BHAGA_BQ_DATASET"] != "bhaga"

    def test_sets_otp_routing_labels(self):
        env = _good_env()
        assert env["BHAGA_RUN_ENV"] == "sandbox"
        assert env["BHAGA_RUN_LABEL"] == "PR#42 fix/item-sales"
        assert env["BHAGA_OTP_TARGET_JOB"] == slr.sandbox_job_resource()

    def test_enables_inline_otp(self):
        # Supervised live run waits for the code inline (existing webhook delivers).
        assert _good_env()["BHAGA_OTP_ASSUME_READY"] == "1"

    def test_routes_to_staging_sheets(self):
        env = _good_env()
        assert env["BHAGA_STAGING_BHAGA_MODEL_SID"] == "SID_MODEL"
        assert env["BHAGA_STAGING_BHAGA_SQUARE_RAW_SID"] == "SID_SQUARE"

    def test_window_vars_and_ignore_halt_set(self):
        """window_from/to injects BHAGA_WINDOW_* and sets BHAGA_IGNORE_HALT=1."""
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(),
            refresh_date="2026-05-31",
            store="palmetto",
            run_label="test",
            window_from="2026-05-04",
            window_to="2026-05-31",
        )
        assert env["BHAGA_WINDOW_FROM"] == "2026-05-04"
        assert env["BHAGA_WINDOW_TO"] == "2026-05-31"
        assert env["BHAGA_IGNORE_HALT"] == "1"

    def test_no_window_no_ignore_halt(self):
        """When no window is set, BHAGA_IGNORE_HALT and window vars must be absent."""
        env = _good_env()  # no window_from / window_to
        assert "BHAGA_IGNORE_HALT" not in env
        assert "BHAGA_WINDOW_FROM" not in env
        assert "BHAGA_WINDOW_TO" not in env

    def test_fresh_scrape_points_read_bucket_at_sandbox(self):
        """fresh_scrape forces cache READS off prod so the run scrapes upstream."""
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(),
            refresh_date="2026-05-31",
            store="palmetto",
            run_label="test",
            fresh_scrape=True,
        )
        assert env["BHAGA_GCS_CACHE_BUCKET"] == slr.SANDBOX_CACHE_WRITE_BUCKET
        assert env["BHAGA_GCS_CACHE_BUCKET"] != "bhaga-scrape-cache"

    def test_no_fresh_scrape_leaves_read_bucket_default(self):
        """Without fresh_scrape, reads still hit prod (normal sandbox behavior)."""
        assert "BHAGA_GCS_CACHE_BUCKET" not in _good_env()

    def test_sheet_from_bq_sets_canonical_flag(self):
        """sheet_from_bq enables the BQ-canonical model path."""
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(),
            refresh_date="2026-05-31",
            store="palmetto",
            run_label="test",
            sheet_from_bq=True,
        )
        assert env["BHAGA_SHEET_FROM_BQ"] == "1"

    def test_no_sheet_from_bq_leaves_flag_unset(self):
        """Without sheet_from_bq, the legacy Sheet-computed model path is used."""
        assert "BHAGA_SHEET_FROM_BQ" not in _good_env()


class TestAssertIsolation:
    def test_passes_for_good_env(self):
        slr.assert_sandbox_isolation(_good_env())

    def test_blocks_non_staging_mode(self):
        env = _good_env()
        env["BHAGA_SHEET_MODE"] = "prod"
        with pytest.raises(RuntimeError, match="staging"):
            slr.assert_sandbox_isolation(env)

    def test_blocks_prod_cache_bucket(self):
        env = _good_env()
        env["BHAGA_GCS_CACHE_WRITE_BUCKET"] = "bhaga-scrape-cache"
        with pytest.raises(RuntimeError, match="bucket"):
            slr.assert_sandbox_isolation(env)

    def test_blocks_prod_runs_collection(self):
        env = _good_env()
        env["BHAGA_FIRESTORE_COLLECTION"] = "runs"
        with pytest.raises(RuntimeError, match="collection"):
            slr.assert_sandbox_isolation(env)

    def test_blocks_prod_bq_dataset(self):
        env = _good_env()
        env["BHAGA_BQ_DATASET"] = "bhaga"
        with pytest.raises(RuntimeError, match="dataset"):
            slr.assert_sandbox_isolation(env)

    def test_blocks_missing_bq_dataset(self):
        env = _good_env()
        del env["BHAGA_BQ_DATASET"]
        with pytest.raises(RuntimeError, match="dataset"):
            slr.assert_sandbox_isolation(env)

    def test_blocks_missing_staging_sheet(self):
        env = _good_env()
        del env["BHAGA_STAGING_BHAGA_MODEL_SID"]
        with pytest.raises(RuntimeError, match="staging sheet"):
            slr.assert_sandbox_isolation(env)


class TestEnvFlagArgs:
    def test_uses_custom_delimiter_for_comma_safe_values(self):
        flag = slr.env_flag_args({"A": "1", "B": "x,y"})
        assert flag[0] == "--set-env-vars"
        assert flag[1].startswith("^@^")
        assert "B=x,y" in flag[1]
        # Pairs are @-joined so the embedded comma in B is not a separator.
        assert "A=1@B=x,y" in flag[1]


# Cloud Run v2 `jobs describe` shape.
_PROD_JOB_V2 = {
    "template": {
        "template": {
            "serviceAccount": "bhaga-runner@jarvis-bhaga-prod.iam.gserviceaccount.com",
            "containers": [
                {
                    "env": [
                        {"name": "STORE", "value": "palmetto"},
                        {"name": "SQUARE_PW", "valueSource": {
                            "secretKeyRef": {"secret": "square-password", "version": "latest"}}},
                        {"name": "ADP_PW", "valueSource": {
                            "secretKeyRef": {"secret": "adp-password", "version": "3"}}},
                    ]
                }
            ],
        }
    }
}

# KRM/v1 shape (what `gcloud run jobs describe --format=json` actually emits here):
# deep nesting, valueFrom.secretKeyRef with name/key, resources.limits, timeoutSeconds.
_PROD_JOB_KRM = {
    "apiVersion": "run.googleapis.com/v1",
    "kind": "Job",
    "spec": {"template": {"spec": {"template": {"spec": {
        "serviceAccountName": "bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com",
        "maxRetries": 0,
        "timeoutSeconds": 3600,
        "containers": [{
            "image": "us-central1-docker.pkg.dev/jarvis-bhaga-prod/jarvis-images/bhaga-orchestrator:abc",
            "resources": {"limits": {"cpu": "2", "memory": "2Gi"}},
            "env": [
                {"name": "STORE", "value": "palmetto"},
                {"name": "SLACK_BOT_TOKEN", "valueFrom": {
                    "secretKeyRef": {"name": "slack-bot-token", "key": "latest"}}},
                {"name": "CLICKUP_PAT", "valueFrom": {
                    "secretKeyRef": {"name": "jarvis-clickup-palmetto-pat", "key": "2"}}},
            ],
        }],
    }}}}},
}


class TestSecretInheritanceV2:
    def test_parses_v2_secret_bindings(self):
        flags = slr.parse_secret_flags(_PROD_JOB_V2)
        assert flags[0] == "--set-secrets"
        assert "SQUARE_PW=square-password:latest" in flags[1]
        assert "ADP_PW=adp-password:3" in flags[1]
        assert "STORE" not in flags[1]  # plain env not mirrored as a secret

    def test_parses_v2_service_account(self):
        assert slr.parse_service_account(_PROD_JOB_V2) == (
            "bhaga-runner@jarvis-bhaga-prod.iam.gserviceaccount.com"
        )


class TestSecretInheritanceKRM:
    def test_parses_krm_secret_bindings(self):
        flags = slr.parse_secret_flags(_PROD_JOB_KRM)
        assert flags[0] == "--set-secrets"
        assert "SLACK_BOT_TOKEN=slack-bot-token:latest" in flags[1]
        assert "CLICKUP_PAT=jarvis-clickup-palmetto-pat:2" in flags[1]
        assert "STORE" not in flags[1]

    def test_parses_krm_service_account(self):
        assert slr.parse_service_account(_PROD_JOB_KRM) == (
            "bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com"
        )

    def test_parses_krm_resources_timeout_retries(self):
        flags = slr.parse_resource_flags(_PROD_JOB_KRM)
        assert "--cpu" in flags and flags[flags.index("--cpu") + 1] == "2"
        assert "--memory" in flags and flags[flags.index("--memory") + 1] == "2Gi"
        assert "--task-timeout" in flags and flags[flags.index("--task-timeout") + 1] == "3600s"
        assert "--max-retries" in flags and flags[flags.index("--max-retries") + 1] == "0"


class _FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class TestScenarioScoping:
    def test_skip_steps_become_env(self):
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(), refresh_date="2026-05-31", store="palmetto",
            run_label="x", skip_steps=["adp", "reviews", "model"],
        )
        assert env["BHAGA_SKIP_ADP"] == "1"
        assert env["BHAGA_SKIP_REVIEWS"] == "1"
        assert env["BHAGA_SKIP_MODEL"] == "1"
        assert "BHAGA_SKIP_KDS" not in env  # only what was requested
        slr.assert_sandbox_isolation(env)  # scoping doesn't break isolation

    def test_no_skip_steps(self):
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(), refresh_date="2026-05-31", store="palmetto",
            run_label="x",
        )
        assert not any(k.startswith("BHAGA_SKIP_") for k in env)


class TestItemSalesVerification:
    def test_ok_when_rows_present(self, monkeypatch):
        def fake_gcloud(args, check=True, capture=False):
            if args[:2] == ["storage", "ls"]:
                return _FakeProc(0, "gs://b/2026-05-31/square/items-2026-05-31-2026-06-01.csv\n")
            if args[:2] == ["storage", "cp"]:
                with open(args[3], "w") as fh:
                    fh.write("name,qty\nLatte,3\nMocha,2\n")
                return _FakeProc(0, "")
            return _FakeProc(1, "")
        monkeypatch.setattr(slr, "_gcloud", fake_gcloud)
        ok, msg = slr.verify_item_sales("2026-05-31")
        assert ok and "2 data rows" in msg

    def test_fails_when_no_item_sales_file(self, monkeypatch):
        monkeypatch.setattr(slr, "_gcloud", lambda *a, **k: _FakeProc(0, ""))
        ok, msg = slr.verify_item_sales("2026-05-31")
        assert not ok and "NOT available" in msg

    def test_fails_when_header_only(self, monkeypatch):
        def fake_gcloud(args, check=True, capture=False):
            if args[:2] == ["storage", "ls"]:
                return _FakeProc(0, "gs://b/2026-05-31/square/items-x.csv\n")
            if args[:2] == ["storage", "cp"]:
                with open(args[3], "w") as fh:
                    fh.write("name,qty\n")  # header only — no data
                return _FakeProc(0, "")
            return _FakeProc(1, "")
        monkeypatch.setattr(slr, "_gcloud", fake_gcloud)
        ok, msg = slr.verify_item_sales("2026-05-31")
        assert not ok and "0 data rows" in msg


class TestParsersHandleEmpty:
    def test_no_secrets_yields_empty(self):
        assert slr.parse_secret_flags({"template": {"template": {"containers": [{"env": []}]}}}) == []
        assert slr.parse_secret_flags({}) == []

    def test_service_account_absent(self):
        assert slr.parse_service_account({}) is None

    def test_resource_flags_absent(self):
        assert slr.parse_resource_flags({}) == []


class TestPlainEnvInheritance:
    def test_parses_plain_env_skips_secrets(self):
        base = slr.parse_env_vars(_PROD_JOB_KRM)
        # plain runtime config carried over...
        assert base["STORE"] == "palmetto"
        # ...secret-sourced env vars are NOT (they ride via --set-secrets)
        assert "SLACK_BOT_TOKEN" not in base
        assert "CLICKUP_PAT" not in base

    def test_real_prod_keys_inherited(self):
        # mirrors the actual prod job: the loader needs BHAGA_SECRETS_BACKEND=gcp
        job = {"spec": {"template": {"spec": {"template": {"spec": {"containers": [{"env": [
            {"name": "BHAGA_SECRETS_BACKEND", "value": "gcp"},
            {"name": "BHAGA_STATE_BACKEND", "value": "firestore"},
            {"name": "GCP_PROJECT", "value": "jarvis-bhaga-prod"},
            {"name": "BHAGA_DM_CHANNEL", "value": "D0B67MW6J02"},
            {"name": "BHAGA_HEADLESS", "value": "1"},
            {"name": "SLACK_BOT_TOKEN", "valueFrom": {"secretKeyRef": {"name": "t", "key": "latest"}}},
        ]}]}}}}}}
        base = slr.parse_env_vars(job)
        assert base["BHAGA_SECRETS_BACKEND"] == "gcp"
        assert base["BHAGA_STATE_BACKEND"] == "firestore"
        assert base["BHAGA_DM_CHANNEL"] == "D0B67MW6J02"
        assert "SLACK_BOT_TOKEN" not in base

    def test_base_env_merged_but_isolation_overlay_wins(self):
        base = {
            "BHAGA_SECRETS_BACKEND": "gcp",
            "BHAGA_DM_CHANNEL": "D0B67MW6J02",
            "BHAGA_STATE_BACKEND": "firestore",
        }
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(),
            refresh_date="2026-05-31", store="palmetto", run_label="PR#9 fix",
            base_env=base,
        )
        # base runtime config is present (fixes the FileNotFoundError)...
        assert env["BHAGA_SECRETS_BACKEND"] == "gcp"
        assert env["BHAGA_DM_CHANNEL"] == "D0B67MW6J02"
        # ...and the isolation overlay still applies and passes the guard.
        assert env["BHAGA_SHEET_MODE"] == "staging"
        slr.assert_sandbox_isolation(env)
