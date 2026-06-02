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
        assert env["REFRESH_DATE"] == "2026-05-31"

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


# Cloud Run v2 `jobs describe --format=json` shape (trimmed to what we parse).
_PROD_JOB_JSON = {
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


class TestSecretInheritance:
    def test_parses_secret_bindings(self):
        flags = slr.parse_secret_flags(_PROD_JOB_JSON)
        assert flags[0] == "--set-secrets"
        assert "SQUARE_PW=square-password:latest" in flags[1]
        assert "ADP_PW=adp-password:3" in flags[1]
        # Plain (non-secret) env vars are not mirrored as secrets.
        assert "STORE" not in flags[1]

    def test_no_secrets_yields_empty(self):
        assert slr.parse_secret_flags({"template": {"template": {"containers": [{"env": []}]}}}) == []
        assert slr.parse_secret_flags({}) == []

    def test_parses_service_account(self):
        assert slr.parse_service_account(_PROD_JOB_JSON) == (
            "bhaga-runner@jarvis-bhaga-prod.iam.gserviceaccount.com"
        )

    def test_service_account_absent(self):
        assert slr.parse_service_account({}) is None
