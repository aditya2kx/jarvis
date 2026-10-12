#!/usr/bin/env python3
"""What a deploy should rerun so recent days are complete and current.

deploy.yml reruns the dates in a PR's ``Retry-Dates:`` trailer. This plans the
rest, over the last ``--lookback`` CT days (through yesterday):

- **Raw holes** — a day with no ``square_daily_rollup``, ``adp_shifts`` or
  ``square_kds_daily`` row (store-profile ``closed_dates`` excepted). One
  full-scrape execution covers min..max hole: one portal login, at most one OTP
  prompt.
- **Model rebuild** — the merged PR changed a file the model build imports
  (``model_sources``) or a store profile: one recompute-only execution that
  rebuilds from the last closed pay period's start, so days built by the old
  code are rebuilt.
- **Rate refresh** — a recent puncher has no wage rate (a new hire, or a rate
  the scraper lost), or the merged PR changed the rate scraper: one full scrape
  of yesterday, since every scrape re-reads all punchers' Payroll info rates.
  A hole scrape already does this, so it is not added twice.
- **Gap nights** — latest run failed, never ran, or no ``model_daily`` row,
  with raw data present: recompute-only, unless the rebuild already covers it.
- **Partial nights** — the run exited 0 but a non-fatal step failed
  (``status='partial'``, steps in ``failed_step``). A missed day-scoped step is a
  gap night. A missed snapshot step (ADP schedule / timecard gaps / pay info,
  inventory, order reco, Plaid) only matters on the latest night, since any later
  run refreshes it: ADP ones join the rate-refresh scrape, the rest recompute.
  ADP skipped on OTP and KDS failures are found as raw holes instead.

Prints one ``trigger_dated_refresh.py`` argument line per execution. Read-only;
on any BQ error prints nothing and exits 0, so it can never fail a deploy.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOOKBACK_DAYS = 14
MAX_RECOMPUTES = 7
MODEL_ENTRY = "agents/bhaga/scripts/materialize_model_bq.py"
STORE_PROFILES = "agents/bhaga/knowledge-base/store-profiles/"
RATE_SCRAPER = frozenset({
    "skills/adp_run_automation/pay_info_backend.py",
    "skills/adp_run_automation/wage_rate_history.py",
})
ADP_SNAPSHOT_STEPS = frozenset({
    "adp_schedule", "adp_timecard_gaps", "adp_pay_info", "adp_earnings", "adp_liability",
})
SNAPSHOT_STEPS = ADP_SNAPSHOT_STEPS | {"ingest_inventory", "refresh_order_reco", "plaid_sync"}
# raw_holes finds these from adp_shifts / square_kds_daily, which converge on data;
# a scrape window records a run only for its last date.
DATA_CHECKED_STEPS = frozenset({"adp", "square_kds"})
_PROJECT = os.environ.get("GCP_PROJECT", "jarvis-bhaga-prod")
_DATASET = os.environ.get("BHAGA_BQ_DATASET", "bhaga")


def _days(today: datetime.date, lookback: int) -> list[str]:
    return [(today - datetime.timedelta(days=b)).isoformat() for b in range(lookback, 0, -1)]


def gap_dates(
    *,
    latest_status: dict[str, str],
    model_days: set[str],
    today: datetime.date,
    lookback: int = LOOKBACK_DAYS,
) -> list[str]:
    """Nights in ``[today - lookback, today - 1]`` whose latest run failed,
    that never ran, or that have no ``model_daily`` row."""
    return [d for d in _days(today, lookback)
            if latest_status.get(d) in (None, "failed") or d not in model_days]


def raw_holes(
    *,
    square_days: set[str],
    adp_days: set[str],
    today: datetime.date,
    lookback: int = LOOKBACK_DAYS,
    closed: frozenset[str] = frozenset(),
    kds_days: set[str] | None = None,
) -> list[str]:
    """Days missing any raw source (a closed day is not a hole)."""
    return [d for d in _days(today, lookback)
            if d not in closed and (d not in square_days or d not in adp_days
                                    or (kds_days is not None and d not in kds_days))]


def partial_replays(partial: dict[str, str], latest_night: str | None) -> tuple[list[str], bool]:
    """For nights whose latest run was ``partial``: (nights to recompute, rescrape ADP?)."""
    recompute: list[str] = []
    rescrape = False
    for d, failed in sorted(partial.items()):
        steps = {s.strip() for s in (failed or "").split(",") if s.strip()}
        latest = d == latest_night
        if steps - SNAPSHOT_STEPS - DATA_CHECKED_STEPS or (
                latest and steps & (SNAPSHOT_STEPS - ADP_SNAPSHOT_STEPS)):
            recompute.append(d)
        if latest and steps & ADP_SNAPSHOT_STEPS:
            rescrape = True
    return recompute, rescrape


def model_sources(entry: str = MODEL_ENTRY, root: pathlib.Path = ROOT) -> set[str]:
    """Repo files the model build imports, transitively (paths relative to root)."""
    seen: set[str] = set()
    todo = [entry]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        tree = ast.parse((root / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            else:
                continue
            for name in names:
                base = name.replace(".", "/")
                for cand in (f"{base}.py", f"{base}/__init__.py"):
                    if (root / cand).is_file():
                        todo.append(cand)
    return seen


def touches_model(changed: list[str], sources: set[str]) -> bool:
    return any(
        f in sources or f.startswith(STORE_PROFILES)
        for f in changed
        if not pathlib.PurePosixPath(f).name.startswith("test_")
    )


def plan(
    *,
    gaps: list[str],
    holes: list[str],
    rebuild_from: str | None,
    yesterday: str,
    exclude: set[str] = frozenset(),
    refresh_rates: bool = False,
) -> list[list[str]]:
    """``trigger_dated_refresh.py`` argument lists, one per execution."""
    runs: list[list[str]] = []
    if holes:
        runs.append(["--date", max(holes), "--force-scrape", "--window-from", min(holes)])
    elif refresh_rates:
        runs.append(["--date", yesterday, "--force-scrape"])
    # Executions run concurrently, so a rebuild rides on the scrape rather than
    # racing it with a second materialize. The model scope ends at --date, so the
    # scrape must then run through yesterday.
    if rebuild_from and runs:
        runs[0][1] = yesterday
        runs[0] += ["--model-scope-from", rebuild_from]
    elif rebuild_from:
        runs.append(["--date", yesterday, "--force-recompute",
                     "--model-scope-from", rebuild_from])
    recompute = [d for d in gaps
                 if d not in holes and d not in exclude
                 and not (rebuild_from and d >= rebuild_from)]
    runs += [["--date", d, "--force-recompute"] for d in recompute[-MAX_RECOMPUTES:]]
    return runs


def _fetch(lookback: int, today: datetime.date) -> dict[str, object]:
    from google.cloud import bigquery  # noqa: PLC0415

    client = bigquery.Client(project=_PROJECT)
    since = (today - datetime.timedelta(days=lookback)).isoformat()
    t = f"`{_PROJECT}.{_DATASET}"

    def days(table: str, col: str) -> set[str]:
        return {r["d"] for r in client.query(
            f"SELECT DISTINCT CAST({col} AS STRING) d FROM {t}.{table}` WHERE {col} >= '{since}'"
        ).result()}

    # The table, not vw_pipeline_runs: that view is the dashboard's last 30 runs, so on busy
    # rerun days older nights drop out of it and would read as never-ran.
    runs = client.query(
        f"SELECT CAST(run_date AS STRING) d, status, failed_step FROM {t}.pipeline_runs` "
        f"WHERE run_date >= '{since}' "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY run_date ORDER BY started_at_utc DESC) = 1"
    ).result()
    unrated = client.query(
        f"WITH p AS (SELECT DISTINCT COALESCE(NULLIF(TRIM(canonical_name), ''), employee_id) n "
        f"FROM {t}.adp_punches` WHERE date >= '{since}') "
        f"SELECT p.n FROM p LEFT JOIN {t}.adp_wage_rates` r "
        f"ON p.n = r.employee_id OR p.n = r.canonical_name "
        f"WHERE p.n IS NOT NULL AND r.wage_rate_dollars IS NULL ORDER BY 1"
    ).result()
    runs = list(runs)
    return {
        "status": {r["d"]: r["status"] for r in runs},
        "partial": {r["d"]: r["failed_step"] for r in runs if r["status"] == "partial"},
        "kds": days("square_kds_daily", "date_local"),
        "model": days("model_daily", "date"),
        "square": days("square_daily_rollup", "date_local"),
        "adp": days("adp_shifts", "date"),
        "unrated": [r["n"] for r in unrated],
    }


def closed_period_start(profile: dict, today: datetime.date) -> str | None:
    """Start of the pay period before the one containing ``today``."""
    from skills.adp_run_automation.wage_rate_history import pay_period_start  # noqa: PLC0415

    anchor = profile.get("adp_run", {}).get("pay_periods_anchor_end_date")
    if not anchor:
        return None
    open_start = pay_period_start(today, anchor_end=datetime.date.fromisoformat(anchor))
    return pay_period_start(open_start - datetime.timedelta(days=1),
                            anchor_end=datetime.date.fromisoformat(anchor)).isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    ap.add_argument("--store", default="palmetto")
    ap.add_argument("--changed-files", default=None,
                    help="File listing the merged PR's changed paths, one per line.")
    ap.add_argument("--exclude", default="",
                    help="Space/comma-separated dates already rerun via Retry-Dates.")
    args = ap.parse_args(argv)

    today = datetime.datetime.now(ZoneInfo("America/Chicago")).date()
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    try:
        profile = json.loads((ROOT / STORE_PROFILES / f"{args.store}.json").read_text())
        data = _fetch(args.lookback, today)
        changed = (pathlib.Path(args.changed_files).read_text().split()
                   if args.changed_files else [])
        rebuild_from = (closed_period_start(profile, today)
                        if touches_model(changed, model_sources()) else None)
        from skills.store_profile import load_exclusions  # noqa: PLC0415
        excluded = set(load_exclusions(args.store).get("permanent") or [])
    except Exception as exc:  # noqa: BLE001
        print(f"[detect_gap_dates] BREADCRUMB gap_scan_failed err={type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 0
    gaps = gap_dates(latest_status=data["status"], model_days=data["model"],
                     today=today, lookback=args.lookback)
    partial_recompute, adp_snapshot_stale = partial_replays(
        data["partial"], max(data["status"], default=None))
    window = set(_days(today, args.lookback))
    gaps = sorted(set(gaps) | (set(partial_recompute) & window))
    holes = raw_holes(square_days=data["square"], adp_days=data["adp"], today=today,
                      lookback=args.lookback,
                      closed=frozenset(profile.get("closed_dates") or []),
                      kds_days=data["kds"])
    exclude = set(args.exclude.replace(",", " ").split())
    unrated = [n for n in data["unrated"] if n not in excluded]
    scraper_changed = bool(RATE_SCRAPER & set(changed))
    runs = plan(gaps=gaps, holes=holes, rebuild_from=rebuild_from,
                yesterday=yesterday, exclude=exclude,
                refresh_rates=bool(unrated) or scraper_changed or adp_snapshot_stale)
    print(f"[detect_gap_dates] gaps={gaps} raw_holes={holes} partial={data['partial']} "
          f"adp_snapshot_stale={adp_snapshot_stale} "
          f"model_rebuild_from={rebuild_from} unrated={unrated} "
          f"rate_scraper_changed={scraper_changed} runs={len(runs)}", file=sys.stderr)
    for r in runs:
        print(" ".join(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
