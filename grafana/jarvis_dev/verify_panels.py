#!/usr/bin/env python3
"""Per-panel verification harness for the Jarvis Development Grafana dashboard.

Runs each BigQuery panel's ``rawSql`` through Grafana's ``/api/ds/query``
endpoint. Stackdriver/GCM panels are skipped (they need a live Cloud Monitoring
connection — verify those visually after deploy).

Usage (from repo root):
    python3 grafana/jarvis_dev/verify_panels.py
    python3 grafana/jarvis_dev/verify_panels.py --fail-on-empty

Environment / auth: GRAFANA_API_TOKEN env var (CI) or Keychain (local).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from skills.grafana_cloud_provisioning.provision import (  # noqa: E402
    _KEYCHAIN_ACCOUNT_DEFAULT,
    get_api_token,
)
from skills.grafana_cloud_provisioning.register import (  # noqa: E402
    get_datasource_uid,
)

_DASHBOARD_JSON = pathlib.Path(__file__).parent / "dashboard.json"
_BQ_DS_NAME = "BHAGA BigQuery"
_BQ_DS_TYPE = "grafana-bigquery-datasource"


def _resolve_token(org_slug: str) -> str:
    token = os.environ.get("GRAFANA_API_TOKEN", "").strip() or get_api_token(org_slug)
    if not token:
        raise SystemExit(
            f"No Grafana API token. Set GRAFANA_API_TOKEN or store in Keychain "
            f"(service=grafana-cloud-api-token, account={org_slug})."
        )
    return token


def _template_defaults(dashboard: dict) -> dict[str, str]:
    defaults: dict[str, str] = {}
    for var in dashboard.get("templating", {}).get("list", []):
        if var.get("type") in ("textbox", "custom", "query"):
            defaults[var["name"]] = str((var.get("current") or {}).get("value", ""))
    return defaults


def _substitute(sql: str, variables: dict[str, str]) -> str:
    for name in sorted(variables, key=len, reverse=True):
        val = variables[name]
        sql = re.sub(r"\$\{" + re.escape(name) + r"\}", val, sql)
        sql = re.sub(r"\$" + re.escape(name) + r"\b", val, sql)
    return sql


def _iter_bq_panels(dashboard: dict):
    """Yield (section_title, panel) for BQ panels with rawSql targets."""
    section = "(none)"
    for panel in dashboard.get("panels", []):
        if panel.get("type") == "row":
            section = panel.get("title", "(row)")
            for child in panel.get("panels", []) or []:
                if child.get("targets") and _is_bq_panel(child):
                    yield section, child
            continue
        if panel.get("targets") and _is_bq_panel(panel):
            yield section, panel


def _is_bq_panel(panel: dict) -> bool:
    for t in panel.get("targets", []) or []:
        ds = t.get("datasource") or {}
        if ds.get("type") == _BQ_DS_TYPE or t.get("rawSql"):
            return True
    return False


def _run_query(grafana_url: str, token: str, ds_uid: str, sql: str) -> dict:
    body = {
        "queries": [{
            "refId": "A",
            "datasource": {"type": _BQ_DS_TYPE, "uid": ds_uid},
            "rawSql": sql,
            "format": "table",
        }],
        "from": "now-2y",
        "to": "now",
    }
    req = urllib.request.Request(
        f"{grafana_url}/api/ds/query",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}

    result = (payload.get("results") or {}).get("A", {})
    if result.get("error"):
        return {"error": str(result["error"])[:200]}
    frames = result.get("frames") or []
    if not frames:
        return {"rows": 0, "first": None, "last": None}
    values = (frames[0].get("data") or {}).get("values") or []
    if not values or not values[0]:
        return {"rows": 0, "first": None, "last": None}
    col0 = values[0]
    return {"rows": len(col0), "first": col0[0], "last": col0[-1]}


def main() -> int:
    cli = argparse.ArgumentParser(
        description="Verify Jarvis Development Grafana BQ panels return data"
    )
    cli.add_argument(
        "--org-slug",
        default=os.environ.get("GRAFANA_ORG_SLUG", _KEYCHAIN_ACCOUNT_DEFAULT),
    )
    cli.add_argument("--fail-on-empty", action="store_true",
                     help="Exit non-zero if any BQ panel returns 0 rows")
    args = cli.parse_args()

    org_slug = args.org_slug
    grafana_url = f"https://{org_slug}.grafana.net"
    token = _resolve_token(org_slug)

    ds_uid = get_datasource_uid(org_slug=org_slug, name=_BQ_DS_NAME)
    if not ds_uid:
        print(
            f"ERROR: BigQuery datasource '{_BQ_DS_NAME}' not found in Grafana.",
            file=sys.stderr,
        )
        return 2
    print(f"[verify] org={org_slug}  datasource_uid={ds_uid}")
    print("[verify] NOTE: stackdriver/GCM panels are skipped — verify visually in UI.\n")

    dashboard = json.loads(_DASHBOARD_JSON.read_text())
    variables = _template_defaults(dashboard)

    hdr = "{:<25} {:>4} {:<7} {:>7}  {}"
    print(hdr.format("SECTION", "ID", "STATUS", "ROWS", "TITLE"))
    print("-" * 110)

    n_error = n_empty = n_ok = 0
    for section, panel in _iter_bq_panels(dashboard):
        target = panel["targets"][0]
        sql = _substitute(target.get("rawSql", ""), variables)
        res = _run_query(grafana_url, token, ds_uid, sql)
        title = panel.get("title", "")
        if "error" in res:
            status = "ERROR"
            n_error += 1
            note = res["error"]
        elif res["rows"] == 0:
            status = "EMPTY"
            n_empty += 1
            note = "(no data yet — expected for Deploys row until first deploy event lands)"
        else:
            status = "OK"
            n_ok += 1
            note = f"first={res['first']}  last={res['last']}"
        print(hdr.format(section[:25], panel.get("id", ""), status, res.get("rows", "-"), title[:45]))
        if note:
            print(f"{'':>40}↳ {note}")

    print("-" * 110)
    print(f"[verify] OK={n_ok}  EMPTY={n_empty}  ERROR={n_error}")

    if n_error:
        print("[verify] FAIL: one or more BQ panels errored.", file=sys.stderr)
        return 1
    if args.fail_on_empty and n_empty:
        print("[verify] FAIL: empty panels detected (--fail-on-empty).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
