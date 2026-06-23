#!/usr/bin/env python3
"""skills/square_api/auth — Square OAuth token storage + auto-refresh.

The nightly job needs a valid Square access token without any human in the
loop. Square access tokens expire after 30 days; code-flow refresh tokens are
multi-use and never expire. We store BOTH (plus the app id/secret) as a single
JSON secret and refresh the access token whenever it is within REFRESH_WINDOW
of expiry (Square recommends refreshing every <=7 days).

Secret layout — ONE Secret Manager secret per store, ``square_<store>_oauth``,
JSON payload::

    {
      "application_id":     "sq0idp-...",
      "application_secret": "<from the console OAuth page>",
      "access_token":       "EAA...",
      "refresh_token":      "...",
      "expires_at":         "2026-07-11T00:00:00Z",   # RFC3339
      "merchant_id":        "..."
    }

Read path mirrors the rest of the repo: ``cred_registry.get_secret`` resolves
from GCP Secret Manager when ``BHAGA_SECRETS_BACKEND=gcp`` (else Keychain).
Writing a refreshed token adds a NEW secret version (prod: the job's SA needs
``roles/secretmanager.secretVersionAdder``; laptop: operator ADC).

Never raises on a transient refresh error without a clear, actionable message —
the orchestrator's failure path surfaces it to Slack (breadcrumb-on-failure).
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.credentials import registry as cred_registry

# Production Connect API host. Override for sandbox via SQUARE_API_BASE.
DEFAULT_API_BASE = "https://connect.squareup.com"
# Square API version pinned for stable response shapes.
SQUARE_VERSION = "2026-05-20"
# Refresh when the access token is within this many days of expiry (or expired).
REFRESH_WINDOW = datetime.timedelta(days=7)

_GCP_PROJECT = "jarvis-bhaga-prod"


class SquareAuthError(RuntimeError):
    """Raised when a token cannot be loaded or refreshed."""


def _secret_name(store: str) -> str:
    return f"square_{store.lower()}_oauth"


def api_base() -> str:
    """Connect API base URL. Sandbox: set SQUARE_API_BASE."""
    return os.environ.get("SQUARE_API_BASE", DEFAULT_API_BASE).rstrip("/")


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_expires_at(value: str) -> datetime.datetime:
    """Parse Square's RFC3339 expires_at into an aware UTC datetime."""
    s = (value or "").strip()
    if not s:
        # No expiry recorded — treat as already expired so we refresh.
        return _now_utc() - datetime.timedelta(days=1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return _now_utc() - datetime.timedelta(days=1)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def load_oauth_secret(store: str = "palmetto") -> dict:
    """Load and parse the OAuth secret JSON for ``store``."""
    name = _secret_name(store)
    try:
        raw = cred_registry.get_secret(name)
    except Exception as exc:  # noqa: BLE001
        raise SquareAuthError(
            f"Could not read Square OAuth secret {name!r}: {exc}. "
            f"Run `python3 -m skills.square_api.grant --store {store}` once to "
            f"create it (BHAGA_SECRETS_BACKEND controls keychain vs GCP)."
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SquareAuthError(
            f"Square OAuth secret {name!r} is not valid JSON: {exc}"
        ) from exc
    missing = [
        k for k in ("application_id", "application_secret", "access_token", "refresh_token")
        if not data.get(k)
    ]
    if missing:
        raise SquareAuthError(
            f"Square OAuth secret {name!r} is missing required fields: {missing}"
        )
    return data


def save_oauth_secret(store: str, data: dict) -> None:
    """Write a NEW version of the OAuth secret (GCP Secret Manager).

    Used after a refresh. Requires secretVersionAdder on the secret. On
    keychain backend this updates the Keychain item in place.
    """
    name = _secret_name(store)
    payload = json.dumps(data, sort_keys=True)
    backend = os.environ.get("BHAGA_SECRETS_BACKEND", "keychain").lower()

    if backend == "gcp":
        try:
            from google.cloud import secretmanager
        except ImportError as exc:  # noqa: BLE001
            raise SquareAuthError(
                "google-cloud-secret-manager is required to write the refreshed "
                "Square token."
            ) from exc
        client = secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{_GCP_PROJECT}/secrets/{name}"
        try:
            client.get_secret(request={"name": secret_path})
        except Exception:  # noqa: BLE001
            client.create_secret(
                request={
                    "parent": f"projects/{_GCP_PROJECT}",
                    "secret_id": name,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        client.add_secret_version(
            request={"parent": secret_path, "payload": {"data": payload.encode("utf-8")}}
        )
        return

    # Keychain backend (laptop): store under a stable service/account.
    entry = cred_registry.lookup(name) or {}
    service = entry.get("service", f"jarvis-square-{store.lower()}")
    account = entry.get("account", "SQUARE_OAUTH")
    cred_registry.add_keychain(
        name, service=service, account=account, password=payload,
        portal="square", notes="Square OAuth token JSON (access+refresh).",
    )


def _post_token(data: dict, payload: dict) -> dict:
    """POST to /oauth2/token and return the parsed response."""
    url = f"{api_base()}/oauth2/token"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Square-Version", SQUARE_VERSION)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SquareAuthError(
            f"Square token endpoint returned {exc.code}: {detail[:400]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SquareAuthError(f"Square token request failed: {exc}") from exc


def refresh_access_token(store: str, data: dict) -> dict:
    """Refresh the access token using the refresh token; persist the new state.

    Returns the updated secret dict. Code flow returns the SAME refresh token.
    """
    resp = _post_token(data, {
        "client_id": data["application_id"],
        "client_secret": data["application_secret"],
        "grant_type": "refresh_token",
        "refresh_token": data["refresh_token"],
    })
    access = resp.get("access_token")
    if not access:
        raise SquareAuthError(
            f"Square refresh response had no access_token: {json.dumps(resp)[:300]}"
        )
    updated = dict(data)
    updated["access_token"] = access
    updated["expires_at"] = resp.get("expires_at", data.get("expires_at", ""))
    # Code flow returns the same refresh token, but honor it if present.
    if resp.get("refresh_token"):
        updated["refresh_token"] = resp["refresh_token"]
    if resp.get("merchant_id"):
        updated["merchant_id"] = resp["merchant_id"]
    save_oauth_secret(store, updated)
    print(f"[square_api.auth] refreshed access token for {store} "
          f"(expires_at={updated.get('expires_at')})")
    return updated


def get_access_token(store: str = "palmetto") -> str:
    """Return a valid Square access token, refreshing if near expiry.

    This is the function the export/KDS modules call before any API request.
    """
    data = load_oauth_secret(store)
    expires_at = _parse_expires_at(data.get("expires_at", ""))
    if expires_at - _now_utc() <= REFRESH_WINDOW:
        data = refresh_access_token(store, data)
    return data["access_token"]


def merchant_id(store: str = "palmetto") -> str:
    return load_oauth_secret(store).get("merchant_id", "")


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Square OAuth token helper")
    sub = cli.add_subparsers(dest="cmd")
    p_show = sub.add_parser("status", help="Show token expiry (no secret values).")
    p_show.add_argument("--store", default="palmetto")
    p_refresh = sub.add_parser("refresh", help="Force a token refresh now.")
    p_refresh.add_argument("--store", default="palmetto")
    args = cli.parse_args()

    if args.cmd == "status":
        d = load_oauth_secret(args.store)
        exp = _parse_expires_at(d.get("expires_at", ""))
        delta = exp - _now_utc()
        print(json.dumps({
            "store": args.store,
            "merchant_id": d.get("merchant_id", ""),
            "expires_at": d.get("expires_at", ""),
            "days_until_expiry": round(delta.total_seconds() / 86400, 2),
            "needs_refresh": delta <= REFRESH_WINDOW,
        }, indent=2))
    elif args.cmd == "refresh":
        d = load_oauth_secret(args.store)
        refresh_access_token(args.store, d)
        print("refreshed.")
    else:
        cli.print_help()
