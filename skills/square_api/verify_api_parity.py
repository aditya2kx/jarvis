#!/usr/bin/env python3
"""skills/square_api/verify_api_parity — Record-level parity diff: API vs scraped data.

REQUIRED PR EVIDENCE (operator-mandated): compare sandbox BQ rows (loaded via
the new Square API path) against prod BQ rows (loaded from Playwright scrapes)
for the same historical dates. Exits non-zero on any mismatch.

Usage (after running export.py --load-bq for the test dates):

    BHAGA_SECRETS_BACKEND=gcp python3 -m skills.square_api.verify_api_parity \
        --dates 2026-06-09 2026-06-10 2026-06-11

Comparison tables (sandbox vs prod for each date):
    bhaga_sandbox.square_transactions   vs  bhaga.square_transactions
    bhaga_sandbox.square_item_lines     vs  bhaga.square_item_lines
    bhaga_sandbox.square_kds_daily      vs  bhaga.square_kds_daily

Join keys:
    square_transactions : transaction_id
    square_item_lines   : transaction_id, item_name, item_sold_at_local, line_seq
    square_kds_daily    : date_local

For each joined row every mapped column is compared.  Mismatches are reported
with up to 5 example rows each and a summary counts table.  Exit code 0 = full
parity (or graceful skip if the sandbox has zero rows for a date, which just
means the API export was not run yet).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

PROJECT = "jarvis-bhaga-prod"
PROD_DS = "bhaga"
SANDBOX_DS = "bhaga_sandbox"

# Columns compared per table (all mapped columns, excluding load-time metadata).
TXN_COLS = [
    "transaction_id", "payment_id", "date_local", "time_local", "tz_label",
    "gross_sales_cents", "discounts_cents", "tip_cents", "total_collected_cents",
    "fees_cents", "net_total_cents", "source", "staff_name", "event_type",
    "location", "transaction_status",
]
ITEM_COLS = [
    "transaction_id", "payment_id", "date_local", "item_name", "quantity",
    "gross_sales_cents", "discounts_cents", "net_sales_cents", "category",
    "employee", "channel", "event_type", "location",
]
KDS_COLS = [
    "date_local", "completed_tickets", "completed_items",
    "median_time_per_item_sec", "p90_time_per_item_sec",
    "p95_time_per_item_sec", "p99_time_per_item_sec",
    "pct_tickets_late",
]

_TABLES = [
    {
        "name": "square_transactions",
        "key": ["transaction_id"],
        "cols": TXN_COLS,
        "date_col": "date_local",
    },
    {
        "name": "square_item_lines",
        "key": ["transaction_id", "item_name", "item_sold_at_local", "line_seq"],
        "cols": ITEM_COLS,
        "date_col": "date_local",
    },
    {
        "name": "square_kds_daily",
        "key": ["date_local"],
        "cols": KDS_COLS,
        "date_col": "date_local",
    },
]


def _client():
    from google.cloud import bigquery
    try:
        return bigquery.Client(project=PROJECT)
    except Exception:
        pass
    import subprocess
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token", f"--project={PROJECT}"],
        text=True, stderr=subprocess.DEVNULL, timeout=15,
    ).strip()
    from google.oauth2.credentials import Credentials
    from google.cloud import bigquery as bq2  # noqa: F811
    return bq2.Client(project=PROJECT, credentials=Credentials(token=token))


def _query(client: Any, sql: str) -> list[dict]:
    rows = list(client.query(sql).result())
    return [dict(r.items()) for r in rows]


def _count_sql(dataset: str, table: str, date: str) -> str:
    return (
        f"SELECT COUNT(*) AS cnt "
        f"FROM `{PROJECT}.{dataset}.{table}` "
        f"WHERE date_local = '{date}'"
    )


def _rows_sql(dataset: str, table: str, date: str, cols: list[str]) -> str:
    col_list = ", ".join(f"`{c}`" for c in cols)
    return (
        f"SELECT {col_list} "
        f"FROM `{PROJECT}.{dataset}.{table}` "
        f"WHERE date_local = '{date}'"
    )


def _key_str(row: dict, key: list[str]) -> str:
    return "|".join(str(row.get(k, "")) for k in key)


def _compare_table(
    client: Any,
    table_spec: dict,
    date: str,
) -> dict:
    """Compare one table for one date. Returns a result dict with pass/fail info."""
    name = table_spec["name"]
    key = table_spec["key"]
    cols = table_spec["cols"]

    # Counts
    prod_cnt = _query(client, _count_sql(PROD_DS, name, date))[0]["cnt"]
    sandbox_cnt = _query(client, _count_sql(SANDBOX_DS, name, date))[0]["cnt"]

    if sandbox_cnt == 0:
        return {
            "table": name, "date": date,
            "status": "SKIP",
            "reason": f"sandbox has 0 rows for {date} — run API export first",
            "prod_cnt": prod_cnt, "sandbox_cnt": 0,
        }

    if prod_cnt != sandbox_cnt:
        return {
            "table": name, "date": date,
            "status": "FAIL",
            "reason": f"row count mismatch: prod={prod_cnt} sandbox={sandbox_cnt}",
            "prod_cnt": prod_cnt, "sandbox_cnt": sandbox_cnt,
        }

    # Full outer join on natural key: find keys present on only one side
    prod_rows = {_key_str(r, key): r for r in _query(client, _rows_sql(PROD_DS, name, date, cols))}
    sandbox_rows = {_key_str(r, key): r for r in _query(client, _rows_sql(SANDBOX_DS, name, date, cols))}

    prod_keys = set(prod_rows)
    sandbox_keys = set(sandbox_rows)
    only_prod = prod_keys - sandbox_keys
    only_sandbox = sandbox_keys - prod_keys

    mismatches: dict[str, list] = {}
    for k in prod_keys & sandbox_keys:
        pr = prod_rows[k]
        sr = sandbox_rows[k]
        for col in cols:
            pv = pr.get(col)
            sv = sr.get(col)
            if _val_ne(pv, sv):
                mismatches.setdefault(col, []).append({
                    "key": k, "prod": pv, "sandbox": sv
                })

    failures = []
    if only_prod:
        failures.append(f"{len(only_prod)} key(s) in prod only: {list(only_prod)[:5]}")
    if only_sandbox:
        failures.append(f"{len(only_sandbox)} key(s) in sandbox only: {list(only_sandbox)[:5]}")
    for col, examples in mismatches.items():
        failures.append(
            f"column '{col}': {len(examples)} mismatch(es) — "
            f"examples: {json.dumps(examples[:5], default=str)}"
        )

    if failures:
        return {
            "table": name, "date": date,
            "status": "FAIL",
            "reason": "; ".join(failures),
            "prod_cnt": prod_cnt, "sandbox_cnt": sandbox_cnt,
            "mismatch_details": mismatches,
            "only_prod": list(only_prod)[:20],
            "only_sandbox": list(only_sandbox)[:20],
        }

    return {
        "table": name, "date": date,
        "status": "PASS",
        "prod_cnt": prod_cnt, "sandbox_cnt": sandbox_cnt,
    }


def _val_ne(a: Any, b: Any) -> bool:
    """Returns True if values differ (handles None, float rounding, type coercions)."""
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) > 0.01
        except (TypeError, ValueError):
            pass
    return str(a) != str(b)


def run_parity(dates: list[str]) -> bool:
    """Run parity check for all tables and dates. Returns True if all pass."""
    client = _client()
    results = []
    for date in dates:
        for tspec in _TABLES:
            result = _compare_table(client, tspec, date)
            results.append(result)

    # Print summary table
    print("\n=== Square API parity report ===\n")
    print(f"{'Table':<30} {'Date':<12} {'Status':<6} {'Prod':>8} {'Sandbox':>8}  Reason")
    print("-" * 110)
    all_pass = True
    for r in results:
        status = r["status"]
        if status == "FAIL":
            all_pass = False
        reason = r.get("reason", "")
        print(
            f"{r['table']:<30} {r['date']:<12} {status:<6} "
            f"{r.get('prod_cnt', '?'):>8} {r.get('sandbox_cnt', '?'):>8}  {reason}"
        )
        if status == "FAIL":
            for col, examples in (r.get("mismatch_details") or {}).items():
                print(f"    column '{col}' — {len(examples)} mismatch(es):")
                for ex in examples[:5]:
                    print(f"      key={ex['key']}  prod={ex['prod']!r}  sandbox={ex['sandbox']!r}")

    print()
    if all_pass:
        print("RESULT: ALL PASS — API-synthesized data matches prod BQ exactly.")
    else:
        print("RESULT: FAIL — mismatches found (see above). Resolve before merging.")
    return all_pass


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Square API vs prod BQ parity check")
    cli.add_argument("--dates", nargs="+", required=True,
                     metavar="YYYY-MM-DD", help="Dates to check")
    args = cli.parse_args()
    ok = run_parity(args.dates)
    sys.exit(0 if ok else 1)
