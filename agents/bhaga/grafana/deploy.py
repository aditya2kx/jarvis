#!/usr/bin/env python3
"""Deploy BHAGA Grafana dashboard to Grafana Cloud.

Reads the dashboard JSON from agents/bhaga/grafana/dashboard.json and pushes
it to Grafana Cloud via the Grafana API.  Also ensures the BigQuery datasource
is configured.

Usage (from repo root):
    python3 agents/bhaga/grafana/deploy.py --org-slug steadyangelfish2985
    python3 agents/bhaga/grafana/deploy.py --org-slug steadyangelfish2985 --datasource-only

Environment:
    GRAFANA_API_TOKEN — if set, overrides Keychain lookup (used in CI/GitHub Actions)
    GRAFANA_ORG_SLUG  — alternative to --org-slug for CI
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from skills.grafana_cloud_provisioning.register import (
    configure_bigquery_datasource,
    push_dashboard,
    get_dashboard_url,
)
from skills.grafana_cloud_provisioning.provision import store_api_token, _KEYCHAIN_ACCOUNT_DEFAULT

_DASHBOARD_JSON = pathlib.Path(__file__).parent / "dashboard.json"
_DEFAULT_ORG = _KEYCHAIN_ACCOUNT_DEFAULT


def _get_token_from_env_or_keychain(org_slug: str) -> str | None:
    """Check GRAFANA_API_TOKEN env var first, then Keychain."""
    env_token = os.environ.get("GRAFANA_API_TOKEN", "").strip()
    if env_token:
        return env_token
    from skills.grafana_cloud_provisioning.provision import get_api_token
    return get_api_token(org_slug)


def main() -> int:
    cli = argparse.ArgumentParser(description="Deploy BHAGA Grafana dashboard")
    cli.add_argument("--org-slug", default=os.environ.get("GRAFANA_ORG_SLUG", _DEFAULT_ORG))
    cli.add_argument("--datasource-only", action="store_true",
                     help="Only configure the BigQuery datasource, skip dashboard push")
    cli.add_argument("--dashboard-only", action="store_true",
                     help="Only push the dashboard, skip datasource configuration")
    cli.add_argument("--create-sa", action="store_true",
                     help="Create the grafana-bq-reader SA + store key in Secret Manager")
    args = cli.parse_args()

    org_slug = args.org_slug
    print(f"[bhaga-grafana-deploy] org_slug={org_slug}")

    # Monkey-patch the token into register if env var is set (CI path)
    env_token = os.environ.get("GRAFANA_API_TOKEN", "").strip()
    if env_token:
        store_api_token(env_token, org_slug)
        print("[bhaga-grafana-deploy] Stored GRAFANA_API_TOKEN from env into Keychain")

    if args.create_sa:
        from skills.grafana_cloud_provisioning.register import create_read_only_sa
        sa_email = create_read_only_sa()
        print(f"[bhaga-grafana-deploy] SA created/updated: {sa_email}")

    if not args.dashboard_only:
        print("[bhaga-grafana-deploy] Configuring BigQuery datasource...")
        ds_result = configure_bigquery_datasource(org_slug=org_slug)
        print(f"[bhaga-grafana-deploy] Datasource: {ds_result.get('message', 'ok')}")

    if not args.datasource_only:
        print("[bhaga-grafana-deploy] Pushing dashboard...")
        dashboard = json.loads(_DASHBOARD_JSON.read_text())
        result = push_dashboard(dashboard, org_slug=org_slug)
        url = result.get("full_url", "unknown")
        print(f"[bhaga-grafana-deploy] Dashboard deployed: {url}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
