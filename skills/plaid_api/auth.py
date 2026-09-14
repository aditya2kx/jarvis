#!/usr/bin/env python3
"""skills/plaid_api/auth — Plaid client_id/secret + per-item access tokens.

Cloud Run prefers env vars ``PLAID_CLIENT_ID`` / ``PLAID_SECRET`` / ``PLAID_ENV``.
Per-item access tokens live in Secret Manager as ``plaid_access_token_<item_id>``.

Does **not** import ``skills.credentials`` (that package is gitignored) so the
bhaga-webhook image can ship ``skills/plaid_api`` alone.
"""

from __future__ import annotations

import os

_GCP_PROJECT = os.environ.get("GCP_PROJECT") or os.environ.get("BQ_PROJECT") or "jarvis-bhaga-prod"

_PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidAuthError(RuntimeError):
    """Raised when Plaid credentials cannot be loaded."""


def _on_cloud_run() -> bool:
    """True inside a Cloud Run service (K_SERVICE) or job (CLOUD_RUN_JOB)."""
    return bool(os.environ.get("K_SERVICE") or os.environ.get("CLOUD_RUN_JOB"))


def plaid_env() -> str:
    """Resolve the Plaid environment.

    Defaulting to sandbox on Cloud Run is a silent-wrong default: the credentials
    come from Secret Manager, which only ever holds the production pair, so an
    unset PLAID_ENV meant sending production keys to sandbox.plaid.com and
    getting back ``INVALID_API_KEYS`` — an error that reads like an expired
    credential and is actually a wrong host. That is exactly what the nightly
    `bhaga-daily-refresh` job did: both Cloud Run *services* set PLAID_ENV
    explicitly, the *job* never did, and it is the job that runs the plaid_sync
    catch-up. Verified 2026-09-13: the stored keys authenticate against
    production (HTTP 200) and are rejected by sandbox.

    So on Cloud Run the default follows the credentials — production. Off Cloud
    Run (laptop, tests) sandbox remains the safe default. An explicit PLAID_ENV
    always wins.
    """
    explicit = (os.environ.get("PLAID_ENV") or "").strip().lower()
    if explicit:
        return explicit
    return "production" if _on_cloud_run() else "sandbox"


def api_base() -> str:
    env = plaid_env()
    if env not in _PLAID_HOSTS:
        raise PlaidAuthError(f"Unknown PLAID_ENV={env!r}; expected sandbox|development|production")
    return _PLAID_HOSTS[env]


def _sm_client():
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


def _read_secret(name: str) -> str:
    client = _sm_client()
    path = f"projects/{_GCP_PROJECT}/secrets/{name}/versions/latest"
    resp = client.access_secret_version(request={"name": path})
    return resp.payload.data.decode("utf-8").strip()


def _get(name: str, env_key: str | None = None) -> str:
    if env_key:
        v = (os.environ.get(env_key) or "").strip()
        if v:
            return v
    try:
        return _read_secret(name)
    except Exception as exc:  # noqa: BLE001
        # Laptop fallback via credentials registry when available.
        try:
            from skills.credentials import registry as cred_registry

            return cred_registry.get_secret(name).strip()
        except Exception as exc2:  # noqa: BLE001
            raise PlaidAuthError(
                f"Could not read Plaid secret {name!r}"
                + (f" (or env {env_key})" if env_key else "")
                + f": sm={exc}; keychain={exc2}. See skills/plaid_api/README.md."
            ) from exc2


def client_id() -> str:
    return _get("plaid_client_id", "PLAID_CLIENT_ID")


def client_secret() -> str:
    return _get("plaid_secret", "PLAID_SECRET")


def access_token_secret_name(item_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in item_id)
    return f"plaid_access_token_{safe}"


def get_access_token(item_id: str) -> str:
    return _get(access_token_secret_name(item_id))


def save_access_token(item_id: str, access_token: str) -> None:
    """Persist Item access_token as a new Secret Manager version (or Keychain)."""
    name = access_token_secret_name(item_id)
    backend = (os.environ.get("BHAGA_SECRETS_BACKEND") or "keychain").lower()
    if backend == "gcp" or os.environ.get("K_SERVICE"):
        client = _sm_client()
        parent = f"projects/{_GCP_PROJECT}/secrets/{name}"
        try:
            client.get_secret(request={"name": parent})
        except Exception:  # noqa: BLE001
            client.create_secret(
                request={
                    "parent": f"projects/{_GCP_PROJECT}",
                    "secret_id": name,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        client.add_secret_version(
            request={
                "parent": parent,
                "payload": {"data": access_token.encode("utf-8")},
            }
        )
        return
    from skills.credentials import registry as cred_registry

    cred_registry.add_keychain(name, access_token, account="plaid")
