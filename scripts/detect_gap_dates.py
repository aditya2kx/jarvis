#!/usr/bin/env python3
"""Recent nights a deploy should recompute without anyone listing them.

deploy.yml reruns the dates in a PR's ``Retry-Dates:`` trailer. This adds the
nights nobody listed: within the last ``--lookback`` CT days (through
yesterday), a night is a gap when its latest pipeline run failed, it never ran,
or it has no ``model_daily`` row. Only dates whose raw data is already in BQ
are printed — those rerun recompute-only (no portal login, no OTP). A night
with no raw data is left to the next nightly, whose gap window re-scrapes it.

Prints one YYYY-MM-DD per line. Read-only; exits 0 with no output on any BQ
error so it can never fail a deploy.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOOKBACK_DAYS = 14
MAX_DATES = 7
_PROJECT = os.environ.get("GCP_PROJECT", "jarvis-bhaga-prod")
_DATASET = os.environ.get("BHAGA_BQ_DATASET", "bhaga")


def gap_dates(
    *,
    latest_status: dict[str, str],
    model_days: set[str],
    today: datetime.date,
    lookback: int = LOOKBACK_DAYS,
) -> list[str]:
    """Nights in ``[today - lookback, today - 1]`` that need a recompute.

    ``latest_status``: run_date → status of that date's most recent run.
    A running/succeeded night with a model row is fine; anything else is a gap.
    """
    out = []
    for back in range(lookback, 0, -1):
        d = (today - datetime.timedelta(days=back)).isoformat()
        status = latest_status.get(d)
        if status == "failed" or status is None or d not in model_days:
            out.append(d)
    return out


def _fetch(lookback: int, today: datetime.date) -> tuple[dict[str, str], set[str]]:
    from google.cloud import bigquery  # noqa: PLC0415

    client = bigquery.Client(project=_PROJECT)
    since = (today - datetime.timedelta(days=lookback)).isoformat()
    runs = client.query(
        f"SELECT CAST(run_date AS STRING) d, status FROM `{_PROJECT}.{_DATASET}.vw_pipeline_runs` "
        f"WHERE run_date >= '{since}' "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY run_date ORDER BY started_at_utc DESC) = 1"
    ).result()
    days = client.query(
        f"SELECT DISTINCT CAST(date AS STRING) d FROM `{_PROJECT}.{_DATASET}.model_daily` "
        f"WHERE date >= '{since}'"
    ).result()
    return {r["d"]: r["status"] for r in runs}, {r["d"] for r in days}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    args = ap.parse_args(argv)

    from trigger_dated_refresh import _date_is_covered  # noqa: PLC0415

    today = datetime.datetime.now(ZoneInfo("America/Chicago")).date()
    try:
        status, model_days = _fetch(args.lookback, today)
    except Exception as exc:  # noqa: BLE001
        print(f"[detect_gap_dates] BREADCRUMB gap_scan_failed err={type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 0
    gaps = gap_dates(latest_status=status, model_days=model_days, today=today,
                     lookback=args.lookback)
    covered = [d for d in gaps if _date_is_covered(d)]
    skipped = sorted(set(gaps) - set(covered))
    print(f"[detect_gap_dates] gaps={gaps} recompute={covered[-MAX_DATES:]} "
          f"left_to_nightly={skipped}", file=sys.stderr)
    for d in covered[-MAX_DATES:]:
        print(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
