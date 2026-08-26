#!/usr/bin/env python3
"""BHAGA Analytics Grafana dashboard — retired (Issue #276).

Operator Console is the store UI. The Jarvis Development cost dashboard is
still deployed from grafana/jarvis_dev/.

Usage:
    python3 agents/bhaga/grafana/deploy.py --delete-bhaga-analytics
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from skills.grafana_cloud_provisioning.provision import _KEYCHAIN_ACCOUNT_DEFAULT
from skills.grafana_cloud_provisioning.register import delete_dashboard

_DEFAULT_ORG = _KEYCHAIN_ACCOUNT_DEFAULT
BHAGA_ANALYTICS_UID = "bhaga-analytics-v1"


def main() -> int:
    cli = argparse.ArgumentParser(
        description="Retire BHAGA Analytics Grafana dashboard (Issue #276)"
    )
    cli.add_argument(
        "--org-slug",
        default=os.environ.get("GRAFANA_ORG_SLUG", _DEFAULT_ORG),
    )
    cli.add_argument(
        "--delete-bhaga-analytics",
        action="store_true",
        help="DELETE Grafana uid bhaga-analytics-v1 (404 = already gone)",
    )
    args = cli.parse_args()
    if not args.delete_bhaga_analytics:
        print(
            "BHAGA Analytics dashboard push is retired (Issue #276). "
            "Operator Console is the store UI. "
            "Jarvis Development: python3 grafana/jarvis_dev/deploy.py. "
            "To unpublish BHAGA Analytics: --delete-bhaga-analytics",
            file=sys.stderr,
        )
        return 2
    msg = delete_dashboard(BHAGA_ANALYTICS_UID, org_slug=args.org_slug)
    print(f"[bhaga-grafana-deploy] uid={BHAGA_ANALYTICS_UID} → {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
