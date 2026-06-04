#!/usr/bin/env python3
"""Grafana Cloud provisioning — signup + API token capture.

Mirrors the pattern of skills/slack_app_provisioning/provision.py:
the skill generates a structured playbook of Playwright actions and
the AI agent executes them through the user-playwright MCP.

Steps covered by this module's playbooks:
  1. Navigate to grafana.com/auth/sign-up, fill credentials, verify email
  2. On the Grafana Cloud dashboard: create a service account + token with
     Admin role, capture the token value
  3. Store the token in macOS Keychain:
       security add-generic-password -s grafana-cloud-api-token -a <org_slug> -w <token>

After provisioning call register.configure_bigquery_datasource() to wire
the BQ service-account key into Grafana Cloud via the Grafana API.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

_KEYCHAIN_SERVICE = "grafana-cloud-api-token"
_KEYCHAIN_ACCOUNT_DEFAULT = "steadyangelfish2985"
_ENV_TOKEN_VAR = "GRAFANA_API_TOKEN"
GRAFANA_CLOUD_URL = "https://grafana.com"
GRAFANA_SIGNUP_URL = "https://grafana.com/auth/sign-up/create-user?pg=hp&plcmt=hero-btn1"


def get_api_token(org_slug: str = _KEYCHAIN_ACCOUNT_DEFAULT) -> str | None:
    """Resolve the Grafana Cloud API token.

    Cloud-native first: the ``GRAFANA_API_TOKEN`` env var (set from the repo
    secret in CI). macOS Keychain is only a *local* fallback — the `security`
    binary does not exist on a Linux CI runner, so Keychain must never be the
    sole source (RUNBOOK §0: operable from a fresh machine with GitHub + GCP
    only, no laptop/Keychain dependency).
    """
    env_token = os.environ.get(_ENV_TOKEN_VAR, "").strip()
    if env_token:
        return env_token
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE,
             "-a", org_slug, "-w"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None  # no `security` (non-macOS / CI) and no env token
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def store_api_token(token: str, org_slug: str = _KEYCHAIN_ACCOUNT_DEFAULT) -> None:
    """Store the Grafana Cloud API token in macOS Keychain (local convenience).

    No-ops gracefully when the `security` binary is absent (e.g. a Linux CI
    runner) — there the token already lives in the ``GRAFANA_API_TOKEN`` env
    var, so persisting to Keychain is neither possible nor needed.
    """
    try:
        subprocess.run(
            ["security", "add-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-a", org_slug, "-w", token,
             "-U"],
            check=True,
        )
    except FileNotFoundError:
        print("[grafana-provision] `security` unavailable (non-macOS) — "
              f"relying on {_ENV_TOKEN_VAR} env var; skipping Keychain store.")
        return
    print(f"[grafana-provision] API token stored in Keychain "
          f"(service={_KEYCHAIN_SERVICE}, account={org_slug})")


def signup_playbook(
    *,
    email: str,
    org_slug: str = _KEYCHAIN_ACCOUNT_DEFAULT,
) -> list[dict]:
    """Return a structured Playwright playbook for Grafana Cloud signup.

    The AI agent executes each step in order using the user-playwright MCP.
    Post-conditions are checked after each action.

    Args:
        email: Email address to use for the Grafana Cloud account.
        org_slug: Grafana org slug (used as Keychain account name).
    """
    return [
        {
            "step": "navigate_signup",
            "action": "browser_navigate",
            "args": {"url": GRAFANA_SIGNUP_URL},
            "post_condition": "Page title contains 'Grafana' or 'Create account'",
        },
        {
            "step": "fill_email",
            "action": "browser_type",
            "selector": "input[name='email'], input[type='email']",
            "value": email,
            "post_condition": "Email field is populated",
        },
        {
            "step": "continue_or_fill_form",
            "action": "browser_click",
            "selector": "button[type='submit'], button:has-text('Continue'), button:has-text('Create account')",
            "post_condition": "Next step in signup flow is visible",
            "note": "If a password field appears, fill it with a secure password stored in Keychain; "
                    "if Google SSO appears, use the personal Google account.",
        },
        {
            "step": "wait_for_dashboard",
            "action": "browser_wait",
            "condition": "URL contains '/orgs/' or '/a/' or '/grafana/' or page shows Grafana Cloud welcome",
            "timeout_ms": 30000,
            "post_condition": "Grafana Cloud dashboard is visible",
            "note": "Email verification may be required — the user must verify email manually.",
        },
        {
            "step": "capture_org_info",
            "action": "browser_evaluate",
            "script": "window.location.href",
            "post_condition": "URL captured — extract org slug from URL path",
            "note": "The org slug is the subdomain or path segment like 'bhaga-palmetto' in "
                    "https://bhaga-palmetto.grafana.net/",
        },
        {
            "step": "navigate_service_accounts",
            "action": "browser_navigate",
            "url_template": "https://{org_slug}.grafana.net/org/serviceaccounts",
            "post_condition": "Service accounts page is shown",
        },
        {
            "step": "create_service_account",
            "action": "browser_click",
            "selector": "button:has-text('Add service account'), button:has-text('New service account')",
            "post_condition": "Service account creation form is visible",
        },
        {
            "step": "fill_service_account_name",
            "action": "browser_type",
            "selector": "input[placeholder*='name'], input[id*='name']",
            "value": "jarvis-agent",
            "post_condition": "Name field populated",
        },
        {
            "step": "set_role_admin",
            "action": "browser_click",
            "selector": "label:has-text('Admin'), input[value='Admin'], option:has-text('Admin')",
            "post_condition": "Admin role selected",
        },
        {
            "step": "submit_service_account",
            "action": "browser_click",
            "selector": "button[type='submit'], button:has-text('Create'), button:has-text('Add')",
            "post_condition": "Service account created, token creation prompt shown",
        },
        {
            "step": "add_token",
            "action": "browser_click",
            "selector": "button:has-text('Add token'), button:has-text('Generate token'), button:has-text('Create token')",
            "post_condition": "Token creation dialog shown",
        },
        {
            "step": "set_token_name",
            "action": "browser_type",
            "selector": "input[placeholder*='name'], input[id*='name']",
            "value": "jarvis-api-token",
            "post_condition": "Token name filled",
        },
        {
            "step": "generate_token",
            "action": "browser_click",
            "selector": "button:has-text('Generate'), button:has-text('Create'), button[type='submit']",
            "post_condition": "Token value is displayed on screen — COPY IT IMMEDIATELY",
        },
        {
            "step": "capture_token",
            "action": "browser_evaluate",
            "script": "document.querySelector('code, [data-testid*=\"token\"], input[type=\"text\"]')?.value || document.querySelector('code, [data-testid*=\"token\"]')?.textContent",
            "post_condition": "Token string captured (starts with 'glc_' or 'eyJ')",
        },
        {
            "step": "store_token_in_keychain",
            "action": "python_call",
            "fn": "skills.grafana_cloud_provisioning.provision.store_api_token",
            "args_template": {"token": "<captured_token>", "org_slug": org_slug},
            "post_condition": "Token stored in Keychain",
        },
    ]
