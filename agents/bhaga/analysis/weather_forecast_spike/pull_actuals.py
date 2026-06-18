"""Pull daily actuals from BigQuery and write data/actuals.csv.

Read-only BQ query. Requires authenticated `bq` CLI or ADC credentials.
Run from the spike dir or repo root.
"""
from __future__ import annotations

import csv
import datetime
import os
import subprocess
import sys

SPIKE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(SPIKE_DIR, "data", "actuals.csv")

QUERY = """
SELECT
  l.date,
  l.orders,
  l.items_sold,
  l.net_sales,
  COALESCE(m.forecast_exclude, FALSE) AS forecast_exclude
FROM `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` l
LEFT JOIN `jarvis-bhaga-prod.bhaga.model_labor_daily` m
  ON l.date = m.date
ORDER BY l.date
"""


def _derive_dow(date_str: str) -> str:
    d = datetime.date.fromisoformat(date_str)
    return d.strftime("%a")  # Mon, Tue, …


def pull_via_bq_cli() -> list[dict]:
    """Run query via bq CLI, parse CSV response."""
    cmd = [
        "bq", "query",
        "--use_legacy_sql=false",
        "--format=csv",
        "--quiet",
        QUERY.strip(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("Empty result from BQ query")
    reader = csv.DictReader(lines)
    return list(reader)


def pull_via_python_client() -> list[dict]:
    """Fallback: use google.cloud.bigquery client."""
    try:
        from google.cloud import bigquery  # type: ignore
    except ImportError:
        raise ImportError("google-cloud-bigquery not installed; use bq CLI instead")
    client = bigquery.Client(project="jarvis-bhaga-prod")
    rows = list(client.query(QUERY).result())
    return [dict(row) for row in rows]


def main() -> None:
    os.makedirs(os.path.join(SPIKE_DIR, "data"), exist_ok=True)

    print("Pulling actuals from BigQuery…")
    try:
        rows = pull_via_bq_cli()
    except FileNotFoundError:
        print("  bq CLI not found, trying Python client…")
        rows = pull_via_python_client()

    if not rows:
        print("ERROR: no rows returned from BQ", file=sys.stderr)
        sys.exit(1)

    fieldnames = ["date", "dow", "orders", "items_sold", "net_sales", "forecast_exclude"]
    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "date": row["date"],
                "dow": _derive_dow(str(row["date"])),
                "orders": row["orders"],
                "items_sold": row["items_sold"],
                "net_sales": row["net_sales"],
                "forecast_exclude": str(row.get("forecast_exclude", "false")).lower(),
            })

    dates = [r["date"] for r in rows]
    orders = [int(r["orders"]) for r in rows if int(r["orders"]) > 0]
    print(f"  Wrote {len(rows)} rows → {OUT_PATH}")
    print(f"  Date range : {min(dates)} → {max(dates)}")
    print(f"  Operating days (orders>0): {len(orders)}")
    print("Done.")


if __name__ == "__main__":
    main()
