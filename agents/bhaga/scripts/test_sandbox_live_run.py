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

    def test_sheet_from_bq_is_noop_legacy_param(self):
        """sheet_from_bq no longer sets BHAGA_SHEET_FROM_BQ — path is unconditional."""
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(),
            refresh_date="2026-05-31",
            store="palmetto",
            run_label="test",
            sheet_from_bq=True,
        )
        assert "BHAGA_SHEET_FROM_BQ" not in env

    def test_no_sheet_from_bq_leaves_flag_unset(self):
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
    """The gate verifies BigQuery (source of truth); GCS is deprecated for data
    loads and is NOT consulted."""

    def test_ok_when_bq_rows_present(self, monkeypatch):
        monkeypatch.setattr(slr, "_bq_item_line_count", lambda *a, **k: 146)
        ok, msg = slr.verify_item_sales("2026-06-08")
        assert ok and "146 row(s)" in msg and "BQ" in msg

    def test_fails_when_bq_zero_rows(self, monkeypatch):
        monkeypatch.setattr(slr, "_bq_item_line_count", lambda *a, **k: 0)
        ok, msg = slr.verify_item_sales("2026-06-08")
        assert not ok and "NOT available" in msg

    def test_inconclusive_when_bq_unqueryable(self, monkeypatch):
        monkeypatch.setattr(slr, "_bq_item_line_count", lambda *a, **k: None)
        ok, msg = slr.verify_item_sales("2026-06-08")
        assert not ok and "INCONCLUSIVE" in msg

    def test_bq_count_parses_csv(self, monkeypatch):
        captured = {}

        def fake_run(args, capture_output=True, text=True):
            captured["args"] = args
            return _FakeProc(0, "f0_\n146\n")

        monkeypatch.setattr(slr.subprocess, "run", fake_run)
        assert slr._bq_item_line_count("2026-06-08") == 146
        assert captured["args"][0] == "bq" and "square_item_lines" in " ".join(captured["args"])

    def test_bq_count_none_on_query_error(self, monkeypatch):
        monkeypatch.setattr(slr.subprocess, "run", lambda *a, **k: _FakeProc(1, ""))
        assert slr._bq_item_line_count("2026-06-08") is None

    def test_bq_count_none_when_cli_missing(self, monkeypatch):
        def _boom(*a, **k):
            raise FileNotFoundError("bq not installed")

        monkeypatch.setattr(slr.subprocess, "run", _boom)
        assert slr._bq_item_line_count("2026-06-08") is None


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


class TestOtpForceRequestEnvKnob:
    """build_sandbox_env correctly switches between assume-ready and force-reprompt."""

    def test_default_sets_assume_ready(self):
        env = _good_env()
        assert env["BHAGA_OTP_ASSUME_READY"] == "1"
        assert "BHAGA_OTP_FORCE_REQUEST" not in env

    def test_otp_force_request_drops_assume_ready_and_sets_force(self):
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(),
            refresh_date="2026-06-14",
            store="palmetto",
            run_label="PR#58 otp-reprompt",
            otp_force_request=True,
        )
        assert env["BHAGA_OTP_FORCE_REQUEST"] == "1"
        assert "BHAGA_OTP_ASSUME_READY" not in env

    def test_otp_force_request_still_passes_isolation(self):
        env = slr.build_sandbox_env(
            staging_ids=_good_ids(),
            refresh_date="2026-06-14",
            store="palmetto",
            run_label="PR#58 otp-reprompt",
            otp_force_request=True,
        )
        slr.assert_sandbox_isolation(env)


class TestVerifyOtpReprompt:
    """verify_otp_reprompt correctly gates on a refreshed checkpoint."""

    _DATE = "2026-06-14"
    _SEEDED_AT = "2026-06-11T10:00:00-05:00"  # 72h before the run

    def _fresh_checkpoint(self, offset_minutes: int = 5) -> dict:
        """Return a checkpoint whose requested_at is seeded_at + offset_minutes."""
        import datetime
        from zoneinfo import ZoneInfo
        CT = ZoneInfo("America/Chicago")
        seeded = datetime.datetime.fromisoformat(self._SEEDED_AT)
        new_ts = seeded + datetime.timedelta(minutes=offset_minutes)
        return {"requested_at": new_ts.isoformat(), "ready_received": False}

    def test_pass_when_requested_at_advanced(self):
        ok, msg = slr.verify_otp_reprompt(
            self._DATE, self._SEEDED_AT,
            get_pending=lambda _d: self._fresh_checkpoint(offset_minutes=5),
        )
        assert ok, msg
        assert "PASS" in msg
        assert "re-prompt" in msg

    def test_fail_when_no_checkpoint(self):
        ok, msg = slr.verify_otp_reprompt(
            self._DATE, self._SEEDED_AT,
            get_pending=lambda _d: None,
        )
        assert not ok
        assert "FAIL" in msg and "no pending_otp" in msg

    def test_fail_when_ready_received(self):
        checkpoint = self._fresh_checkpoint()
        checkpoint["ready_received"] = True
        ok, msg = slr.verify_otp_reprompt(
            self._DATE, self._SEEDED_AT,
            get_pending=lambda _d: checkpoint,
        )
        assert not ok
        assert "ready_received=True" in msg

    def test_fail_when_requested_at_unchanged(self):
        # Same as seeded_at — daily_refresh deferred to the stale marker.
        checkpoint = {"requested_at": self._SEEDED_AT, "ready_received": False}
        ok, msg = slr.verify_otp_reprompt(
            self._DATE, self._SEEDED_AT,
            get_pending=lambda _d: checkpoint,
        )
        assert not ok
        assert "did NOT advance" in msg

    def test_fail_when_requested_at_before_seeded(self):
        import datetime
        from zoneinfo import ZoneInfo
        CT = ZoneInfo("America/Chicago")
        older = (datetime.datetime.fromisoformat(self._SEEDED_AT)
                 - datetime.timedelta(hours=1)).isoformat()
        checkpoint = {"requested_at": older, "ready_received": False}
        ok, msg = slr.verify_otp_reprompt(
            self._DATE, self._SEEDED_AT,
            get_pending=lambda _d: checkpoint,
        )
        assert not ok
        assert "did NOT advance" in msg
