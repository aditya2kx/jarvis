#!/usr/bin/env python3
"""Per-panel verification harness for the BHAGA Grafana dashboard.

Runs each panel's ``rawSql`` through Grafana's ``/api/ds/query`` endpoint — the
same path the UI uses — against the real BigQuery datasource UID, with the
dashboard's template-variable defaults substituted. Prints a per-section readout
so "No data" panels are obvious, and exits non-zero if any panel errors (e.g.
"Data source not found").

This is the objective check behind the Grafana "No data" fix: after
``deploy.py`` binds the real datasource UID (see ``bind_datasource_uid``), every
BigQuery panel should return rows — except the date-bounded investigation panels
(52/53), which are empty when ``$inv_date`` falls outside the data range.

Usage (from repo root):
    python3 agents/bhaga/grafana/verify_panels.py
    python3 agents/bhaga/grafana/verify_panels.py --var inv_date=2026-05-30
    python3 agents/bhaga/grafana/verify_panels.py --fail-on-empty

Environment / auth: identical to deploy.py — GRAFANA_API_TOKEN env var (CI) or
Keychain (local). Read-only: only runs SELECTs through the read-only BQ SA.
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from skills.grafana_cloud_provisioning.provision import (  # noqa: E402
    _KEYCHAIN_ACCOUNT_DEFAULT,
    get_api_token,
)
from skills.grafana_cloud_provisioning.register import (  # noqa: E402
    get_bigquery_datasource_uid,
)

_DASHBOARD_JSON = pathlib.Path(__file__).parent / "dashboard.json"
_DS_TYPE = "grafana-bigquery-datasource"


def _resolve_token(org_slug: str) -> str:
    token = os.environ.get("GRAFANA_API_TOKEN", "").strip() or get_api_token(org_slug)
    if not token:
        raise SystemExit(
            "No Grafana API token. Set GRAFANA_API_TOKEN or store it in Keychain "
            f"(service=grafana-cloud-api-token, account={org_slug})."
        )
    return token


def _template_defaults(dashboard: dict) -> dict[str, str]:
    """Map textbox template-var name -> default value from the dashboard JSON."""
    defaults: dict[str, str] = {}
    for var in dashboard.get("templating", {}).get("list", []):
        if var.get("type") == "textbox":
            defaults[var["name"]] = str((var.get("current") or {}).get("value", ""))
    return defaults


def _substitute(sql: str, variables: dict[str, str]) -> str:
    """Replace ${name} and $name occurrences with their values (longest-first)."""
    for name in sorted(variables, key=len, reverse=True):
        val = variables[name]
        sql = re.sub(r"\$\{" + re.escape(name) + r"\}", val, sql)
        sql = re.sub(r"\$" + re.escape(name) + r"\b", val, sql)
    return sql


def _iter_query_panels(dashboard: dict):
    """Yield (section_title, panel) for every non-row panel with a rawSql target."""
    section = "(none)"
    for panel in dashboard.get("panels", []):
        if panel.get("type") == "row":
            section = panel.get("title", "(row)")
            for child in panel.get("panels", []) or []:
                if child.get("targets"):
                    yield section, child
            continue
        if panel.get("targets"):
            yield section, panel


def _run_query(grafana_url: str, token: str, ds_uid: str, sql: str) -> dict:
    """POST one rawSql to /api/ds/query; return {rows, first, last} or {error}."""
    body = {
        "queries": [{
            "refId": "A",
            "datasource": {"type": _DS_TYPE, "uid": ds_uid},
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
    except Exception as e:  # noqa: BLE001 — surface any transport error per panel
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
    cli = argparse.ArgumentParser(description="Verify BHAGA Grafana panels return data")
    cli.add_argument("--org-slug", default=os.environ.get("GRAFANA_ORG_SLUG", _KEYCHAIN_ACCOUNT_DEFAULT))
    cli.add_argument("--var", action="append", default=[],
                     help="Override a template var, e.g. --var inv_date=2026-05-30")
    cli.add_argument("--fail-on-empty", action="store_true",
                     help="Exit non-zero if any panel returns 0 rows (not just errors)")
    args = cli.parse_args()

    org_slug = args.org_slug
    grafana_url = f"https://{org_slug}.grafana.net"
    token = _resolve_token(org_slug)

    ds_uid = get_bigquery_datasource_uid(org_slug=org_slug)
    if not ds_uid:
        print("ERROR: BigQuery datasource not found in Grafana.", file=sys.stderr)
        return 2
    print(f"[verify] org={org_slug} datasource_uid={ds_uid}")

    dashboard = json.loads(_DASHBOARD_JSON.read_text())
    variables = _template_defaults(dashboard)
    for override in args.var:
        if "=" not in override:
            raise SystemExit(f"--var must be name=value, got {override!r}")
        k, v = override.split("=", 1)
        variables[k] = v
    print(f"[verify] template vars: {variables}\n")

    rows_fmt = "{:<22} {:>4} {:<7} {:>7}  {}"
    print(rows_fmt.format("SECTION", "ID", "STATUS", "ROWS", "TITLE"))
    print("-" * 100)

    n_error = 0
    n_empty = 0
    n_ok = 0
    for section, panel in _iter_query_panels(dashboard):
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
            note = ""
        else:
            status = "OK"
            n_ok += 1
            note = f"first={res['first']} last={res['last']}"
        print(rows_fmt.format(section[:22], panel.get("id", ""), status,
                              res.get("rows", "-"), title[:40]))
        if note:
            print(f"{'':>36}↳ {note}")

    print("-" * 100)
    print(f"[verify] OK={n_ok}  EMPTY={n_empty}  ERROR={n_error}")
    if n_error:
        print("[verify] FAIL: one or more panels errored (datasource/SQL).", file=sys.stderr)
        return 1
    if args.fail_on_empty and n_empty:
        print("[verify] FAIL: one or more panels returned 0 rows (--fail-on-empty).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
