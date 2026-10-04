"""Approve a pending ADP unavailability request from the console — Issue #337.

  python3 -m agents.bhaga.scripts.adp_unavailability_approve --store palmetto --row-key '<row_key>'

Looks up the stored pending ``adp_unavailability`` row, clicks APPROVE on the
one matching card in Team Schedule › Pending requests, and returns. Cloud Run:
daily_refresh early-exits here when ``BHAGA_ADP_UNAVAIL_APPROVE`` holds the
row_key, then runs the schedule-only refresh so BQ shows the approved entry.
``--dry-run`` matches the card without clicking.
"""

from __future__ import annotations

import argparse
import sys


def load_pending(row_key: str) -> dict | None:
    from google.cloud import bigquery

    from core.datastore import fq, get_client

    client = get_client()
    if client is None:
        raise RuntimeError("BigQuery datastore is not enabled (BHAGA_DATASTORE=bigquery)")
    rows = list(client.query(
        f"SELECT raw_employee_name, CAST(first_date AS STRING) AS first_date, from_time, to_time"
        f" FROM {fq('adp_unavailability')} WHERE row_key = @key AND status = 'pending'",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("key", "STRING", row_key)]
        ),
    ).result())
    return dict(rows[0]) if rows else None


def run(store: str, row_key: str, *, headless: bool, dry_run: bool, requested_by: str = "") -> int:
    from skills.adp_run_automation import runner as r
    from skills.adp_run_automation import unavailability_approve_backend as ab

    target = load_pending(row_key)
    if target is None:
        print(f"BREADCRUMB adp_unavail_approve row_key={row_key!r} error=no stored pending request")
        return 1
    print(f"[unavail_approve] store={store} by={requested_by or '?'} target={target} dry_run={dry_run}")
    with r.adp_session(store=store, headed=not headless) as (_ctx, page):
        frame = r._open_team_schedule(page)
        page.wait_for_timeout(2500)
        try:
            ab.approve(page, frame, target, dry_run=dry_run)
        except ab.ApproveError as exc:
            print(f"BREADCRUMB adp_unavail_approve row_key={row_key!r} error={type(exc).__name__}: {exc}"[:600])
            return 1
    print(f"[unavail_approve] {'matched' if dry_run else 'approved'} {target['raw_employee_name']} {target['first_date']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--store", default="palmetto")
    p.add_argument("--row-key", required=True)
    p.add_argument("--requested-by", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--headless", action="store_true")
    a = p.parse_args(argv)
    return run(a.store, a.row_key, headless=a.headless, dry_run=a.dry_run, requested_by=a.requested_by)


if __name__ == "__main__":
    sys.exit(main())
