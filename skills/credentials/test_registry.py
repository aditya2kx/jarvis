"""Tests for skills.credentials.registry — dual-backend secrets shim."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import skills.credentials.registry as registry


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Ensure each test starts with keychain (default) backend."""
    monkeypatch.delenv("BHAGA_SECRETS_BACKEND", raising=False)


@pytest.fixture
def mock_registry(tmp_path, monkeypatch):
    """Point the registry at a temp registry.json with test data."""
    reg_data = {
        "test_keychain_cred": {
            "type": "keychain",
            "service": "jarvis-test",
            "account": "testuser",
            "portal": "TestPortal",
        },
        "test_oauth_cred": {
            "type": "oauth_file",
            "path": str(tmp_path / "oauth"),
            "email": "test@example.com",
        },
    }
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(reg_data))
    monkeypatch.setattr(registry, "_REGISTRY_PATH", str(reg_path))
    return reg_data


# ── Backend selection tests ───────────────────────────────────────────


class TestBackendSelection:
    def test_default_backend_is_keychain(self):
        assert registry._secrets_backend() == "keychain"

    def test_env_var_selects_gcp(self, monkeypatch):
        monkeypatch.setenv("BHAGA_SECRETS_BACKEND", "gcp")
        assert registry._secrets_backend() == "gcp"

    def test_env_var_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("BHAGA_SECRETS_BACKEND", "GCP")
        assert registry._secrets_backend() == "gcp"


# ── get_secret with keychain backend ─────────────────────────────────


class TestGetSecretKeychain:
    def test_reads_from_keychain(self, mock_registry, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "s3cr3t_password\n"
        monkeypatch.setattr(registry.subprocess, "run", mock_run)

        result = registry.get_secret("test_keychain_cred")
        assert result == "s3cr3t_password"

        call_args = mock_run.call_args[0][0]
        assert "security" in call_args
        assert "find-generic-password" in call_args
        assert "-s" in call_args
        assert "jarvis-test" in call_args
        assert "-a" in call_args
        assert "testuser" in call_args
        assert "-w" in call_args

    def test_raises_on_keychain_failure(self, mock_registry, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 44
        mock_run.return_value.stderr = "The specified item could not be found"
        monkeypatch.setattr(registry.subprocess, "run", mock_run)

        with pytest.raises(RuntimeError, match="Keychain lookup failed"):
            registry.get_secret("test_keychain_cred")

    def test_raises_on_unknown_name(self, mock_registry):
        with pytest.raises(KeyError, match="not found in registry"):
            registry.get_secret("nonexistent_cred")

    def test_reads_oauth_file(self, mock_registry, tmp_path):
        oauth_dir = tmp_path / "oauth"
        oauth_dir.mkdir()
        creds_file = oauth_dir / ".gdrive-server-credentials.json"
        creds_file.write_text('{"token": "abc123"}')

        result = registry.get_secret("test_oauth_cred")
        assert result == '{"token": "abc123"}'


# ── get_secret with gcp backend ──────────────────────────────────────


class TestGetSecretGCP:
    def test_reads_from_secret_manager(self, mock_registry, monkeypatch):
        monkeypatch.setenv("BHAGA_SECRETS_BACKEND", "gcp")

        mock_client_cls = MagicMock()
        mock_client = mock_client_cls.return_value
        mock_response = MagicMock()
        mock_response.payload.data = b"gcp_secret_value"
        mock_client.access_secret_version.return_value = mock_response

        monkeypatch.setattr(registry, "_secretmanager", MagicMock())
        registry._secretmanager.SecretManagerServiceClient = mock_client_cls

        result = registry.get_secret("test_keychain_cred")
        assert result == "gcp_secret_value"

        mock_client.access_secret_version.assert_called_once_with(
            name="projects/jarvis-bhaga-prod/secrets/test_keychain_cred/versions/latest"
        )

    def test_raises_when_sdk_not_installed(self, mock_registry, monkeypatch):
        monkeypatch.setenv("BHAGA_SECRETS_BACKEND", "gcp")
        monkeypatch.setattr(registry, "_secretmanager", None)

        with pytest.raises(ImportError, match="google-cloud-secret-manager"):
            registry.get_secret("test_keychain_cred")


# ── verify with dual backends ─────────────────────────────────────────


class TestVerifyDualBackend:
    def test_verify_keychain_default(self, mock_registry, monkeypatch):
        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        monkeypatch.setattr(registry.subprocess, "run", mock_run)

        result = registry.verify("test_keychain_cred")
        assert result["ok"] is True
        assert "Keychain" in result["detail"]

    def test_verify_gcp_backend(self, mock_registry, monkeypatch):
        monkeypatch.setenv("BHAGA_SECRETS_BACKEND", "gcp")

        mock_sm = MagicMock()
        mock_client = mock_sm.SecretManagerServiceClient.return_value
        mock_client.access_secret_version.return_value = MagicMock()
        monkeypatch.setattr(registry, "_secretmanager", mock_sm)

        result = registry.verify("test_keychain_cred")
        assert result["ok"] is True
        assert "GCP Secret Manager" in result["detail"]

    def test_verify_gcp_handles_missing(self, mock_registry, monkeypatch):
        monkeypatch.setenv("BHAGA_SECRETS_BACKEND", "gcp")

        mock_sm = MagicMock()
        mock_client = mock_sm.SecretManagerServiceClient.return_value
        mock_client.access_secret_version.side_effect = Exception("NOT_FOUND")
        monkeypatch.setattr(registry, "_secretmanager", mock_sm)

        result = registry.verify("test_keychain_cred")
        assert result["ok"] is False
        assert "NOT_FOUND" in result["detail"]

    def test_verify_not_in_registry(self, mock_registry):
        result = registry.verify("nonexistent")
        assert result["ok"] is False
        assert "Not in registry" in result["detail"]


# ── mirror_to_gcp tests ──────────────────────────────────────────────


class TestMirrorToGCP:
    def test_mirrors_bhaga_secrets_only(self, tmp_path, monkeypatch):
        reg_data = {
            "adp_palmetto_login": {
                "type": "keychain",
                "service": "jarvis-adp-palmetto",
                "account": "test@example.com",
            },
            "schwab": {
                "type": "keychain",
                "service": "jarvis-schwab",
                "account": "aditya.2ky",
            },
        }
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps(reg_data))
        monkeypatch.setattr(registry, "_REGISTRY_PATH", str(reg_path))

        mock_sm = MagicMock()
        mock_client = mock_sm.SecretManagerServiceClient.return_value
        mock_client.get_secret.side_effect = Exception("NOT_FOUND")
        monkeypatch.setattr(registry, "_secretmanager", mock_sm)

        mock_run = MagicMock()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "password123\n"
        monkeypatch.setattr(registry.subprocess, "run", mock_run)

        results = registry.mirror_to_gcp()

        assert results["adp_palmetto_login"] == "mirrored"
        assert "skipped" in results["schwab"]
        mock_client.add_secret_version.assert_called_once()

    def test_raises_without_sdk(self, monkeypatch):
        monkeypatch.setattr(registry, "_secretmanager", None)
        with pytest.raises(ImportError, match="google-cloud-secret-manager"):
            registry.mirror_to_gcp()
