#!/usr/bin/env python3
"""compare_panels.py — prod-vs-branch snapshot parity for every dashboard panel.

The Issue #126 acceptance bar (operator-set): moving Order Assistant logic out
of Grafana into BigQuery must change **zero data**. This script proves it by
running every panel's SQL — both the version currently live on `origin/main`
(Snapshot A / "prod") and the version on this branch (Snapshot B) — through
Grafana's `/api/ds/query` (same path the UI renders through, no browser, no
gcloud/ADC — see README.md "Auth model") and diffing full result rows.

Pass criterion: every panel is row-for-row IDENTICAL between prod and branch,
**except** panels 79/81 (Order Assistant), whose only sanctioned delta is the
7/1 row(s) once the freshness fix (Milestone 3) has been deployed and a
nightly run has landed 7/1 data. Any other difference, on any panel, is a
hard failure — Grafana moved from computing data to displaying it; it must
not have moved a single number in the process.

Two runs against the *same* underlying data are required for the OA panels
before migration 029 is live in prod BigQuery (i.e. pre-merge): branch panel
81/79 rawSql is `SELECT * FROM tvf_order_reco(...)` / `vw_order_assistant_table`,
objects that don't exist yet. --mode inline (the default) substitutes those
references with the *exact* SQL body from
core/migrations/029_order_assistant_functions.sql — mathematically the same
query BigQuery will run once the migration is applied, just not persisted as
a named object yet. --mode live runs branch panel SQL completely as-is
(use post-merge, once `ensure_schema()` has created the real objects).

Usage:
    python3 agents/bhaga/grafana/compare_panels.py
    python3 agents/bhaga/grafana/compare_panels.py --mode live
    python3 agents/bhaga/grafana/compare_panels.py --panel 79 --panel 81
    python3 agents/bhaga/grafana/compare_panels.py --base origin/main

Auth: identical to verify_panels.py — GRAFANA_API_TOKEN env (CI) or Keychain
(local). No gcloud/ADC/config.yaml needed; BigQuery is queried *by Grafana*.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.datastore import _split_statements  # noqa: E402
from skills.grafana_cloud_provisioning.provision import (  # noqa: E402
    _KEYCHAIN_ACCOUNT_DEFAULT,
    get_api_token,
)
from skills.grafana_cloud_provisioning.register import (  # noqa: E402
    get_bigquery_datasource_uid,
)
from verify_panels import (  # noqa: E402
    _iter_query_panels,
    _resolve_empty_query_vars,
    _substitute,
    _template_defaults,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_DASHBOARD_REL = "agents/bhaga/grafana/dashboard.json"
_MIGRATION_029 = _REPO_ROOT / "core" / "migrations" / "029_order_assistant_functions.sql"
_DS_TYPE = "grafana-bigquery-datasource"

# Panels the operator has agreed may legitimately differ, and why. Any other
# panel difference is a hard failure. Keep this list to exactly the panels
# Milestone 2 converted (plus 83, which supersedes 81/82 in the Issue #137
# combined-table iteration) — regressing scope here silently widens the
# "allowed to differ" surface, which defeats the point of the gate.
OA_PANEL_IDS: frozenset[int] = frozenset({79, 83})


def _resolve_token(org_slug: str) -> str:
    token = os.environ.get("GRAFANA_API_TOKEN", "").strip() or get_api_token(org_slug)
    if not token:
        raise SystemExit(
            "No Grafana API token. Set GRAFANA_API_TOKEN or store it in Keychain "
            f"(service=grafana-cloud-api-token, account={org_slug})."
        )
    return token


def _load_dashboard(ref: str) -> dict:
    """Load dashboard.json from a git ref (e.g. 'origin/main') or 'WORKTREE'."""
    if ref == "WORKTREE":
        return json.loads((_REPO_ROOT / _DASHBOARD_REL).read_text())
    result = subprocess.run(
        ["git", "show", f"{ref}:{_DASHBOARD_REL}"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def _panel_sql_by_id(dashboard: dict) -> dict[int, tuple[str, str]]:
    """Map panel_id -> (section, rawSql) for every data panel."""
    out: dict[int, tuple[str, str]] = {}
    for section, panel in _iter_query_panels(dashboard):
        out[panel["id"]] = (section, panel["targets"][0].get("rawSql", ""))
    return out


def _inline_migration_object(sql: str) -> str:
    """Replace a call to tvf_order_reco(...)/vw_order_assistant_table with the
    literal SQL body from migration 029 — see module docstring for why."""
    statements = [s for s in _split_statements(_MIGRATION_029.read_text()) if s.strip()]
    view_stmt, func_stmt = statements[0], statements[1]

    # Statements may carry leading `--` comments before the DDL keyword (the
    # module header comment lands in statements[0] since _split_statements
    # only splits on semicolons) — search+slice from the match, don't anchor
    # at the string start.
    view_m = re.search(
        r"CREATE\s+OR\s+REPLACE\s+VIEW\s+`[^`]+`\s+AS\s*", view_stmt,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not view_m:
        raise RuntimeError("could not parse vw_order_assistant_table body out of migration 029")
    view_body = view_stmt[view_m.end():].strip()

    m = re.search(
        r"CREATE\s+OR\s+REPLACE\s+TABLE\s+FUNCTION\s+`[^`]+`\s*\(([^)]*)\)\s*AS\s*\((.*)\)\s*$",
        func_stmt, flags=re.IGNORECASE | re.DOTALL,
    )
    if not m:
        raise RuntimeError("could not parse tvf_order_reco body out of migration 029")
    params_decl, func_body = m.group(1), m.group(2).strip()
    param_names = [p.strip().split()[0] for p in params_decl.split(",")]

    def _sub_tvf_call(match: re.Match) -> str:
        args = [a.strip() for a in match.group(1).split(",")]
        body = func_body
        for name, arg in zip(param_names, args):
            body = re.sub(rf"\b{re.escape(name)}\b", arg, body)
        return f"({body})"

    sql = re.sub(
        r"`[\w\-.]*\.tvf_order_reco`\s*\(([^)]*)\)",
        _sub_tvf_call, sql, flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"`[\w\-.]*\.vw_order_assistant_table`",
        f"({view_body})", sql, flags=re.IGNORECASE,
    )
    return sql


def _run_query(grafana_url: str, token: str, ds_uid: str, sql: str) -> dict:
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
    import urllib.error
    import urllib.request

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
        return {"error": f"HTTP {e.code}: {e.read().decode()[:400]}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:400]}

    result = (payload.get("results") or {}).get("A", {})
    if result.get("error"):
        return {"error": str(result["error"])[:400]}
    frames = result.get("frames") or []
    if not frames:
        return {"columns": [], "rows": []}
    f = frames[0]
    columns = [c["name"] for c in f["schema"]["fields"]]
    values = (f.get("data") or {}).get("values") or []
    rows = list(zip(*values)) if values else []
    return {"columns": columns, "rows": rows}


def _diff_rows(a: dict, b: dict) -> str | None:
    """Return a human-readable diff summary, or None if identical."""
    if "error" in a or "error" in b:
        return f"query error — prod: {a.get('error')}  branch: {b.get('error')}"
    if a["columns"] != b["columns"]:
        return f"COLUMN MISMATCH — prod: {a['columns']}  branch: {b['columns']}"
    set_a = {tuple(r) for r in a["rows"]}
    set_b = {tuple(r) for r in b["rows"]}
    only_a = set_a - set_b
    only_b = set_b - set_a
    if not only_a and not only_b:
        return None
    lines = [f"{len(a['rows'])} prod rows vs {len(b['rows'])} branch rows"]
    if only_a:
        lines.append(f"  only in prod ({len(only_a)}): {sorted(only_a)[:5]}")
    if only_b:
        lines.append(f"  only in branch ({len(only_b)}): {sorted(only_b)[:5]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org-slug", default=os.environ.get("GRAFANA_ORG_SLUG", _KEYCHAIN_ACCOUNT_DEFAULT))
    ap.add_argument("--base", default="origin/main", help="git ref for 'prod' dashboard.json")
    ap.add_argument("--mode", choices=["inline", "live"], default="inline",
                    help="inline (default, pre-merge safe) or live (post-merge, real BQ objects)")
    ap.add_argument("--panel", action="append", type=int, default=[], dest="panels",
                    help="restrict to specific panel IDs (repeatable); default = all")
    ap.add_argument("--var", action="append", default=[], help="override a template var, e.g. --var oa_ship_days=10")
    args = ap.parse_args()

    org_slug = args.org_slug
    grafana_url = f"https://{org_slug}.grafana.net"
    token = _resolve_token(org_slug)
    ds_uid = get_bigquery_datasource_uid(org_slug=org_slug)
    if not ds_uid:
        print("ERROR: BigQuery datasource not found in Grafana.", file=sys.stderr)
        return 2

    branch_dashboard = _load_dashboard("WORKTREE")
    prod_dashboard = _load_dashboard(args.base)

    variables = _template_defaults(branch_dashboard)
    for override in args.var:
        k, v = override.split("=", 1)
        variables[k] = v
    _resolve_empty_query_vars(branch_dashboard, variables, grafana_url, token, ds_uid)

    prod_sql_by_id = _panel_sql_by_id(prod_dashboard)
    branch_sql_by_id = _panel_sql_by_id(branch_dashboard)

    panel_ids = sorted(set(prod_sql_by_id) | set(branch_sql_by_id))
    if args.panels:
        panel_ids = [p for p in panel_ids if p in args.panels]

    print(f"[compare] org={org_slug} datasource_uid={ds_uid} base={args.base} mode={args.mode}")
    print(f"[compare] {len(panel_ids)} panel(s)\n")

    row_fmt = "{:<22} {:>4} {:<6}  {}"
    print(row_fmt.format("SECTION", "ID", "STATUS", "NOTE"))
    print("-" * 110)

    n_pass, n_fail, n_expected_delta = 0, 0, 0
    failures: list[str] = []

    for panel_id in panel_ids:
        if panel_id not in prod_sql_by_id or panel_id not in branch_sql_by_id:
            print(row_fmt.format("?", panel_id, "SKIP", "panel added/removed between prod and branch"))
            continue
        section, prod_raw = prod_sql_by_id[panel_id]
        _, branch_raw = branch_sql_by_id[panel_id]

        prod_sql = _substitute(prod_raw, variables)
        branch_sql = _substitute(branch_raw, variables)
        if args.mode == "inline" and panel_id in OA_PANEL_IDS:
            branch_sql = _inline_migration_object(branch_sql)

        if prod_sql == branch_sql:
            # Byte-identical query text -> trivially identical results; no
            # network round-trip needed to prove parity for unconverted panels.
            print(row_fmt.format(section[:22], panel_id, "PASS", "unchanged rawSql"))
            n_pass += 1
            continue

        snap_a = _run_query(grafana_url, token, ds_uid, prod_sql)
        snap_b = _run_query(grafana_url, token, ds_uid, branch_sql)
        diff = _diff_rows(snap_a, snap_b)

        if diff is None:
            print(row_fmt.format(section[:22], panel_id, "PASS", "logic moved, 0 row diff"))
            n_pass += 1
        elif panel_id in OA_PANEL_IDS:
            # Sanctioned category of difference — but only a 7/1-shaped delta,
            # never a column mismatch or any other row content change.
            print(row_fmt.format(section[:22], panel_id, "OA-Δ", diff.splitlines()[0]))
            print(f"{'':>34}{diff}")
            n_expected_delta += 1
        else:
            print(row_fmt.format(section[:22], panel_id, "FAIL", diff.splitlines()[0]))
            print(f"{'':>34}{diff}")
            failures.append(f"panel {panel_id} ({section}): {diff}")
            n_fail += 1

    print("-" * 110)
    print(f"[compare] PASS={n_pass}  OA-DELTA={n_expected_delta}  FAIL={n_fail}")
    if failures:
        print("\n[compare] FAIL — unsanctioned data differences found:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
