#!/usr/bin/env python3
"""Deploy the Jarvis Development Grafana dashboard.

Reads grafana/jarvis_dev/dashboard.json and pushes it to Grafana Cloud under
the "Jarvis Development" folder. Reuses the existing "BHAGA BigQuery" datasource
(the grafana-bq-reader SA has project-level bigquery.dataViewer which covers
jarvis_dev — no extra IAM needed).

Usage (from repo root):
    python3 grafana/jarvis_dev/deploy.py
    python3 grafana/jarvis_dev/deploy.py --org-slug steadyangelfish2985

Environment:
    GRAFANA_API_TOKEN — if set, overrides Keychain lookup (used in CI)
    GRAFANA_ORG_SLUG  — alternative to --org-slug for CI
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from skills.grafana_cloud_provisioning.register import (
    get_bigquery_datasource_uid,
    push_dashboard,
)
from skills.grafana_cloud_provisioning.provision import _KEYCHAIN_ACCOUNT_DEFAULT

_DASHBOARD_JSON = pathlib.Path(__file__).parent / "dashboard.json"
_DEFAULT_ORG = _KEYCHAIN_ACCOUNT_DEFAULT
_DS_VAR_NAME = "ds_bigquery"
_DS_DISPLAY_NAME = "BHAGA BigQuery"
_FOLDER_TITLE = "Jarvis Development"


def bind_datasource_uid(dashboard: dict, uid: str, *, var_name: str = _DS_VAR_NAME) -> int:
    """Rewrite the dashboard so every BigQuery datasource ref uses the real UID."""
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
            _rewrite_ref(var.get("datasource"))

    return count


def main() -> int:
    cli = argparse.ArgumentParser(description="Deploy Jarvis Development Grafana dashboard")
    cli.add_argument("--org-slug", default=os.environ.get("GRAFANA_ORG_SLUG", _DEFAULT_ORG))
    args = cli.parse_args()

    org_slug = args.org_slug
    print(f"[jarvis-dev-grafana-deploy] org_slug={org_slug}")

    if os.environ.get("GRAFANA_API_TOKEN", "").strip():
        print("[jarvis-dev-grafana-deploy] Using GRAFANA_API_TOKEN from env.")

    ds_uid = get_bigquery_datasource_uid(org_slug=org_slug)
    if not ds_uid:
        print(
            "[jarvis-dev-grafana-deploy] ERROR: could not resolve BigQuery datasource UID. "
            "Ensure the 'BHAGA BigQuery' datasource is configured (run BHAGA's deploy.py first).",
            file=sys.stderr,
        )
        return 1

    dashboard = json.loads(_DASHBOARD_JSON.read_text())
    bound = bind_datasource_uid(dashboard, ds_uid)
    print(f"[jarvis-dev-grafana-deploy] Bound {bound} datasource ref(s) to uid={ds_uid}")

    result = push_dashboard(dashboard, org_slug=org_slug, folder_title=_FOLDER_TITLE)
    url = result.get("full_url", "unknown")
    print(f"[jarvis-dev-grafana-deploy] Dashboard deployed: {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
