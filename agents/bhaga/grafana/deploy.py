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
    get_bigquery_datasource_uid,
    push_dashboard,
    get_dashboard_url,
)
from skills.grafana_cloud_provisioning.provision import _KEYCHAIN_ACCOUNT_DEFAULT

_DASHBOARD_JSON = pathlib.Path(__file__).parent / "dashboard.json"
_DEFAULT_ORG = _KEYCHAIN_ACCOUNT_DEFAULT
_DS_VAR_NAME = "ds_bigquery"
_DS_DISPLAY_NAME = "BHAGA BigQuery"


def bind_datasource_uid(
    dashboard: dict, uid: str, *, var_name: str = _DS_VAR_NAME
) -> int:
    """Rewrite the dashboard so every BigQuery datasource ref uses the real UID.

    The repo's ``dashboard.json`` keeps panels datasource-agnostic by pointing
    every ``datasource`` at the ``${ds_bigquery}`` template variable. That
    variable is a ``type: datasource`` var whose value is the datasource *name*
    ("BHAGA BigQuery"), but panels reference it as ``"uid": "${ds_bigquery}"`` —
    so Grafana tries to resolve a datasource whose UID equals the *name*, fails
    with "Data source not found", and every panel shows "No data".

    Binding the literal UID at deploy time fixes this without committing an
    environment-specific UID to the repo. We rewrite the panel/target
    ``datasource.uid`` refs, any query-type template variable's own
    ``datasource.uid`` ref (e.g. ``kds_date``), and the ``ds_bigquery`` var's
    ``current`` value.

    Returns the number of datasource refs rewritten.
    """
    placeholder = "${" + var_name + "}"
    count = 0

    def _rewrite_ref(ref: object) -> None:
        nonlocal count
        if isinstance(ref, dict) and ref.get("uid") == placeholder:
            ref["uid"] = uid
            count += 1

    def _walk(panels: list) -> None:
        for panel in panels:
            _rewrite_ref(panel.get("datasource"))
            for target in panel.get("targets", []) or []:
                _rewrite_ref(target.get("datasource"))
            if panel.get("panels"):
                _walk(panel["panels"])

    _walk(dashboard.get("panels", []))

    for var in dashboard.get("templating", {}).get("list", []):
        if var.get("name") == var_name:
            var["current"] = {
                "text": _DS_DISPLAY_NAME,
                "value": uid,
                "selected": False,
            }
        else:
            # query-type vars (e.g. kds_date) carry their own datasource ref;
            # leaving it as ${ds_bigquery} can break the variable's options query.
            _rewrite_ref(var.get("datasource"))

    return count


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

    # No Keychain write needed in CI: get_api_token() resolves GRAFANA_API_TOKEN
    # from the env first (Linux runners have no `security` binary). RUNBOOK §0.
    if os.environ.get("GRAFANA_API_TOKEN", "").strip():
        print("[bhaga-grafana-deploy] Using GRAFANA_API_TOKEN from env.")

    if args.create_sa:
        from skills.grafana_cloud_provisioning.register import create_read_only_sa
        sa_email = create_read_only_sa()
        print(f"[bhaga-grafana-deploy] SA created/updated: {sa_email}")

    ds_uid: str | None = None
    if not args.dashboard_only:
        print("[bhaga-grafana-deploy] Configuring BigQuery datasource...")
        ds_result = configure_bigquery_datasource(org_slug=org_slug)
        ds_uid = ds_result.get("uid")
        print(f"[bhaga-grafana-deploy] Datasource: {ds_result.get('message', 'ok')} (uid={ds_uid})")

    if not args.datasource_only:
        print("[bhaga-grafana-deploy] Pushing dashboard...")
        dashboard = json.loads(_DASHBOARD_JSON.read_text())

        # Bind panels to the real datasource UID (see bind_datasource_uid). On a
        # --dashboard-only run we still need the UID, so look it up.
        if ds_uid is None:
            ds_uid = get_bigquery_datasource_uid(org_slug=org_slug)
        if not ds_uid:
            print("[bhaga-grafana-deploy] ERROR: could not resolve BigQuery "
                  "datasource UID; dashboard panels would show 'No data'. "
                  "Run with datasource configuration enabled.", file=sys.stderr)
            return 1
        bound = bind_datasource_uid(dashboard, ds_uid)
        print(f"[bhaga-grafana-deploy] Bound {bound} datasource ref(s) to uid={ds_uid}")

        result = push_dashboard(dashboard, org_slug=org_slug)
        url = result.get("full_url", "unknown")
        print(f"[bhaga-grafana-deploy] Dashboard deployed: {url}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
