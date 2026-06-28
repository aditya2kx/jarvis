#!/usr/bin/env python3
"""Trigger one dated BHAGA daily_refresh Cloud Run Job execution — smartly.

If the date is already covered by raw Square data in BigQuery, run a
RECOMPUTE-ONLY execution (skip Square/ADP/KDS portal scrapes => no browser, no
OTP) so only the model is rebuilt from changed human inputs (training_shifts,
config). If the date is NOT covered, run a FULL refresh (normal OTP-gated scrape).

Used by .github/workflows/deploy.yml to auto-rerun the dates a merged PR declares
via a "Retry-Dates: YYYY-MM-DD[, ...]" trailer. Uses per-execution container
overrides (run_v2.RunJobRequest.Overrides) so the job definition is NEVER mutated
(a persisted REFRESH_DATE would corrupt future nightlies).

Auth: Application Default Credentials (WIF in CI). No secrets needed.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

_PROJECT = os.environ.get("GCP_PROJECT", "jarvis-bhaga-prod")
_REGION = os.environ.get("BHAGA_REGION", "us-central1")
_JOB = os.environ.get("CLOUD_RUN_JOB_NAME_SHORT", "bhaga-daily-refresh")
_DATASET = os.environ.get("BHAGA_BQ_DATASET", "bhaga")
_JOB_RESOURCE = f"projects/{_PROJECT}/locations/{_REGION}/jobs/{_JOB}"


def _date_is_covered(date_str: str) -> bool:
    """True if raw Square data already covers date_str (=> recompute-only)."""
    from google.cloud import bigquery  # noqa: PLC0415

    client = bigquery.Client(project=_PROJECT)
    sql = f"SELECT MAX(date_local) AS m FROM `{_PROJECT}.{_DATASET}.square_daily_rollup`"
    rows = list(client.query(sql).result())
    max_date = rows[0]["m"] if rows else None
    if max_date is None:
        return False
    return datetime.date.fromisoformat(date_str) <= max_date


def _decide_recompute(date_str: str, *, force_recompute: bool, force_scrape: bool) -> bool:
    if force_recompute:
        return True
    if force_scrape:
        return False
    return _date_is_covered(date_str)


def _build_env_overrides(date_str: str, recompute_only: bool) -> list[tuple[str, str]]:
    """Return the per-execution env overrides as (name, value) tuples."""
    env = [("REFRESH_DATE", date_str)]
    if recompute_only:
        env += [
            ("BHAGA_SKIP_SQUARE", "1"),
            ("BHAGA_SKIP_ADP", "1"),
            ("BHAGA_SKIP_KDS", "1"),
        ]
    # Full-scrape reruns start inline (no READY handshake) in the default gate
    # mode. BHAGA_OTP_FORCE_REQUEST was only meaningful under the legacy
    # BHAGA_OTP_REQUIRE_READY=1 mode and is no longer injected here.
    # Deploy-triggered reruns include the fix that caused the original failure, so
    # bypass the halt breaker unconditionally — a healthy run will auto-clear it.
    env.append(("BHAGA_IGNORE_HALT", "1"))
    return env


def _trigger(date_str: str, recompute_only: bool) -> None:
    from google.cloud import run_v2  # noqa: PLC0415

    env = [run_v2.EnvVar(name=n, value=v) for n, v in _build_env_overrides(date_str, recompute_only)]
    client = run_v2.JobsClient()
    client.run_job(
        request=run_v2.RunJobRequest(
            name=_JOB_RESOURCE,
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(env=env),
                ],
            ),
        ),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True, help="Refresh date YYYY-MM-DD")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--force-recompute", action="store_true",
                      help="Force recompute-only (skip the BQ coverage probe).")
    mode.add_argument("--force-scrape", action="store_true",
                      help="Force a full scrape (skip the BQ coverage probe).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the decision only; do not trigger an execution.")
    args = ap.parse_args(argv)

    try:
        datetime.date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid --date {args.date!r} (want YYYY-MM-DD)", file=sys.stderr)
        return 2

    recompute_only = _decide_recompute(
        args.date, force_recompute=args.force_recompute, force_scrape=args.force_scrape
    )
    mode_str = "recompute-only (no portal login)" if recompute_only else "full refresh (scrape + OTP)"
    print(f"[trigger_dated_refresh] date={args.date} mode={mode_str} job={_JOB_RESOURCE}")
    if args.dry_run:
        print("[trigger_dated_refresh] --dry-run: not triggering.")
        return 0
    _trigger(args.date, recompute_only)
    print(f"[trigger_dated_refresh] execution queued for {args.date}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
