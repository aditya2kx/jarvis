#!/usr/bin/env python3
"""BHAGA daily refresh orchestrator (incremental).

End-to-end nightly flow:
    1. Read `data_window_end` from the Model sheet's `config` tab.
    2. Compute gap window = [data_window_end + 1 .. refresh_date].
       If gap is empty (already covered), no scrape needed.
    3. Scrape Square Transactions CSV for the gap, then dedupe-append rows
       into the canonical master CSV (transactions-master.csv).
    4. Scrape ADP Timecard XLSX for pay periods overlapping the gap.
       (Skipped via --skip-timecard until iframe selectors are calibrated.)
    5. (When --include-rates) Scrape ADP Earnings & Hours V1 XLSX.
       Defaults: ON only on Mondays and Tuesdays.
    6. Mirror local scrapes into the canonical raw Google Sheets
       (bhaga_adp_raw, bhaga_square_raw) via backfill_from_downloads. Per
       architecture contract, all downstream code reads only from these.
    7. Run update_model_sheet to refresh the 5 Model workbook tabs.
       (Reads from raw sheets, NOT local files.)
    8. Run process_reviews to fetch Google reviews from ClickUp, allocate
       bonuses, and rebuild review_bonus_period on the Model sheet.
       (Skippable via --skip-reviews; idempotent on rerun.)
    9. Send success heartbeat to BHAGA Slack DM.

INCREMENTAL CONTRACT (per skill spec):
    - The Model sheet's config tab is the source of truth for "what we've
      already pulled". Never re-scrape what we already have.
    - refresh_date defaults to today_ct because the shop closes by 8 PM CT
      and nightly fires at 21:00 CT, so today is complete by then.
    - To rebuild from scratch: delete extracted/downloads/transactions-master.csv
      AND reset data_window_end in the config tab to data_window_start.

On any step failure: capture step name + exception + traceback, fire a
failure_alert DM, and exit non-zero so the launchd wrapper does NOT write
the success marker (the next 15-min wakeup will retry).

CLI:
    python3 -m agents.bhaga.scripts.daily_refresh --store palmetto
    python3 -m agents.bhaga.scripts.daily_refresh --store palmetto --date 2026-05-16
    python3 -m agents.bhaga.scripts.daily_refresh --store palmetto --skip-rates
    python3 -m agents.bhaga.scripts.daily_refresh --store palmetto --include-rates
    python3 -m agents.bhaga.scripts.daily_refresh --store palmetto --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from agents.bhaga.notify import failure_alert, info_ping, success_heartbeat
from core.config_loader import refresh_access_token
from skills.adp_run_automation.runner import download_earnings, download_timecard
from skills.square_tips.runner import download_transactions

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
STORE_PROFILES = PROJECT_ROOT / "agents" / "bhaga" / "knowledge-base" / "store-profiles"
DOWNLOAD_DIR = PROJECT_ROOT / "extracted" / "downloads"
MASTER_TXN_CSV = DOWNLOAD_DIR / "transactions-master.csv"
CT = ZoneInfo("America/Chicago")


def _load_profile(store: str) -> dict:
    return json.loads((STORE_PROFILES / f"{store}.json").read_text())


def _today_ct() -> datetime.date:
    """The current CT date. Used as the inclusive refresh_date.

    Nightly fires at 21:00 CT, which is AFTER the shop's 20:00 CT close,
    so today is a complete business day and safe to pull."""
    return datetime.datetime.now(CT).date()


def _read_data_window_end_from_sheet(
    *, spreadsheet_id: str, store: str
) -> datetime.date | None:
    """Read the `data_window_end` value from the Model sheet's config tab.

    Returns None if the sheet/tab/key is missing or empty (fresh install).
    """
    token = refresh_access_token(store)
    rng = urllib.parse.quote("config!A1:C200", safe="!:")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/"
        f"{spreadsheet_id}/values/{rng}"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  [config-read] could not read config tab: {exc}")
        return None
    for row in data.get("values", []):
        if row and row[0] == "data_window_end" and len(row) >= 2 and row[1]:
            try:
                return datetime.date.fromisoformat(row[1].strip())
            except ValueError:
                print(f"  [config-read] data_window_end has unparseable value: {row[1]!r}")
                return None
    return None


def _consolidate_into_master(
    *, gap_csv: pathlib.Path, master_csv: pathlib.Path = MASTER_TXN_CSV
) -> tuple[int, int]:
    """Merge a freshly-downloaded gap CSV into the canonical master CSV.

    Strategy:
      - If master doesn't exist yet, the gap CSV becomes the master verbatim.
      - Otherwise, read both, dedupe by `Transaction ID` (Square's primary
        key), keep ALL master rows + any gap rows whose ID is not in master,
        and write back in original column order.

    Returns (rows_in_master_after, rows_added_from_gap).
    """
    if not gap_csv.exists():
        raise FileNotFoundError(f"gap CSV not found: {gap_csv}")

    if not master_csv.exists():
        master_csv.parent.mkdir(parents=True, exist_ok=True)
        master_csv.write_bytes(gap_csv.read_bytes())
        with master_csv.open("r", newline="") as fh:
            n = sum(1 for _ in csv.reader(fh)) - 1  # minus header
        return (max(n, 0), max(n, 0))

    with master_csv.open("r", newline="") as fh:
        master_rows = list(csv.DictReader(fh))
    with gap_csv.open("r", newline="") as fh:
        gap_reader = csv.DictReader(fh)
        fieldnames = gap_reader.fieldnames or []
        gap_rows = list(gap_reader)

    id_col = "Transaction ID"
    if id_col not in fieldnames:
        raise RuntimeError(
            f"Expected '{id_col}' column in gap CSV; got {fieldnames}"
        )

    existing_ids = {row.get(id_col, "") for row in master_rows if row.get(id_col)}
    new_rows = [r for r in gap_rows if r.get(id_col) and r[id_col] not in existing_ids]

    merged = master_rows + new_rows
    with master_csv.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(merged)

    return (len(merged), len(new_rows))


def _should_run_rates(*, override: str | None) -> bool:
    """Default: only Mon/Tue (Pythonic weekday: 0=Mon, 1=Tue).
    The default Palmetto pay period ends on Sunday, so rate scrapes the next
    two days catch the freshly-issued Credit Card Tips Owed lines."""
    if override == "yes":
        return True
    if override == "no":
        return False
    today = datetime.datetime.now(CT).date()
    return today.weekday() in {0, 1}


def run_step(
    step_name: str,
    fn,
    *,
    refresh_date: str,
    dry_run: bool,
) -> tuple[bool, object]:
    """Run a step; on exception, send Slack failure_alert and return (False, exc).

    Returns (success, return_value_or_exception)."""
    print(f"\n[{step_name}] starting...")
    t0 = time.monotonic()
    if dry_run:
        print(f"[{step_name}] DRY RUN — skipped.")
        return True, None
    try:
        result = fn()
        dt = time.monotonic() - t0
        print(f"[{step_name}] OK ({dt:.1f}s) -> {result}")
        return True, result
    except Exception as exc:  # noqa: BLE001
        dt = time.monotonic() - t0
        print(f"[{step_name}] FAILED after {dt:.1f}s: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            failure_alert(
                step=step_name,
                exception=exc,
                date=refresh_date,
                extra=(
                    f"This step failed after {dt:.1f}s. The next 15-min launchd "
                    "wakeup will retry. If 3+ consecutive failures, re-seed the "
                    "relevant Playwright profile manually."
                ),
            )
        except Exception:  # noqa: BLE001, S110
            pass
        return False, exc


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--date", default=None,
                     help="Refresh date YYYY-MM-DD (inclusive end of incremental window). "
                          "Default: today CT (shop closes at 8pm CT, nightly fires at 9pm).")
    cli.add_argument("--from-date", default=None,
                     help="Override the incremental start date instead of reading config tab. "
                          "Use this for manual gap-fill (e.g. --from-date 2026-05-10 to re-pull last 6 days).")
    cli.add_argument("--interactive", action="store_true",
                     help="Show browser windows (headed). Default: headed too (better than headless for ADP).")
    cli.add_argument("--headless", action="store_true",
                     help="Run browsers headless. Risky for ADP anti-bot.")
    cli.add_argument("--include-rates", choices=["yes", "no", "auto"], default="auto",
                     help="Include the Earnings & Hours rate scrape. auto = Mon/Tue only.")
    cli.add_argument("--skip-rates", action="store_true",
                     help="Shortcut for --include-rates=no.")
    cli.add_argument("--skip-square", action="store_true")
    cli.add_argument("--skip-timecard", action="store_true")
    cli.add_argument("--skip-reviews", action="store_true",
                     help="Skip the Google review bonus refresh step.")
    cli.add_argument("--skip-model", action="store_true",
                     help="Skip the final Model-sheet refresh (raw downloads only).")
    cli.add_argument("--dry-run", action="store_true",
                     help="Print steps but do not actually scrape.")
    cli.add_argument("--no-slack", action="store_true",
                     help="Suppress all Slack messages (overrides notify.py).")
    args = cli.parse_args()

    if args.no_slack:
        os.environ["BHAGA_SLACK_DISABLED"] = "1"

    refresh_date = (
        datetime.date.fromisoformat(args.date) if args.date else _today_ct()
    )
    profile = _load_profile(args.store)
    data_start = datetime.date.fromisoformat(profile["calibration"]["first_data_window"]["start"])
    spreadsheet_id = profile["google_sheets"]["bhaga_model"]["spreadsheet_id"]

    # ---- Incremental window resolution ----------------------------------
    # Source of truth: Model sheet's config tab `data_window_end`.
    # gap = [data_window_end + 1, refresh_date]
    # If the sheet has no entry yet, this is a fresh install -> full backfill
    # from `data_start`. After that first run, all subsequent runs are
    # strictly incremental.
    if args.from_date:
        gap_start = datetime.date.fromisoformat(args.from_date)
        gap_source = "--from-date override"
    elif args.skip_square:
        gap_start = refresh_date
        gap_source = "(square skipped)"
    else:
        prev_end = _read_data_window_end_from_sheet(
            spreadsheet_id=spreadsheet_id, store=args.store
        )
        if prev_end is None:
            gap_start = data_start
            gap_source = f"fresh install -> data_start={data_start}"
        else:
            gap_start = prev_end + datetime.timedelta(days=1)
            gap_source = f"sheet.data_window_end={prev_end} + 1"

    needs_square_scrape = (not args.skip_square) and (gap_start <= refresh_date)

    include_rates = (
        False if args.skip_rates else _should_run_rates(override=args.include_rates if args.include_rates != "auto" else None)
    )

    headed = not args.headless  # default headed

    print(f"\n{'='*60}")
    print(f"BHAGA daily_refresh  store={args.store}  refresh_date={refresh_date.isoformat()}")
    print(f"  gap source:     {gap_source}")
    print(f"  gap window:     {gap_start.isoformat()} → {refresh_date.isoformat()}"
          + ("  (empty — nothing to scrape)" if not needs_square_scrape and not args.skip_square else ""))
    print(f"  include_rates:  {include_rates}")
    print(f"  headed:         {headed}")
    print(f"  dry_run:        {args.dry_run}")
    print(f"{'='*60}")

    t_start = time.monotonic()
    info_ping(
        f"daily refresh starting for {refresh_date.isoformat()} "
        f"(gap={gap_start.isoformat()}..{refresh_date.isoformat()}, "
        f"include_rates={include_rates})"
    )

    failures: list[tuple[str, Exception]] = []
    artifacts: dict[str, pathlib.Path | None] = {
        "square_csv": None, "adp_timecard_xlsx": None, "adp_earnings_xlsx": None,
    }
    master_stats: dict[str, int] = {}

    # Step 1: Square Transactions (incremental) + consolidate into master CSV
    if needs_square_scrape:
        ok, val = run_step(
            "square_transactions",
            lambda: download_transactions(
                start_date=gap_start, end_date=refresh_date,
                store=args.store, headed=headed,
            ),
            refresh_date=refresh_date.isoformat(),
            dry_run=args.dry_run,
        )
        if ok:
            artifacts["square_csv"] = val
            if val is not None and not args.dry_run:
                ok2, val2 = run_step(
                    "consolidate_csv",
                    lambda: _consolidate_into_master(gap_csv=val),
                    refresh_date=refresh_date.isoformat(),
                    dry_run=args.dry_run,
                )
                if ok2 and val2:
                    total, added = val2
                    master_stats = {"master_rows": total, "rows_added": added}
                    print(f"  [consolidate] master={total} rows, added={added} from gap")
                elif not ok2:
                    failures.append(("consolidate_csv", val2))
        else:
            failures.append(("square_transactions", val))
    elif not args.skip_square:
        print("[square_transactions] SKIPPED — already covered through refresh_date.")

    # Step 2: ADP Timecard
    if not args.skip_timecard:
        ok, val = run_step(
            "adp_timecard",
            lambda: download_timecard(store=args.store, headed=headed),
            refresh_date=refresh_date.isoformat(),
            dry_run=args.dry_run,
        )
        if ok:
            artifacts["adp_timecard_xlsx"] = val
        else:
            failures.append(("adp_timecard", val))

    # Step 3: ADP Earnings (conditional)
    if include_rates:
        ok, val = run_step(
            "adp_earnings",
            lambda: download_earnings(store=args.store, headed=headed),
            refresh_date=refresh_date.isoformat(),
            dry_run=args.dry_run,
        )
        if ok:
            artifacts["adp_earnings_xlsx"] = val
        else:
            failures.append(("adp_earnings", val))

    # Step 3b: Push scraped data into the three RAW Google Sheets. The model
    # sheet's contract (per architecture) is: read only from raw sheets, never
    # from local files. So we MUST upsert local scrapes into bhaga_adp_raw +
    # bhaga_square_raw before the model refresh runs. Failure here blocks the
    # model refresh — stale raw sheets would make the model sheet lie.
    failed_steps = {name for name, _ in failures}
    square_ok = (
        "square_transactions" not in failed_steps
        and "consolidate_csv" not in failed_steps
        and not args.skip_square
    )
    raw_sheets_ok = False
    if square_ok or not args.skip_timecard:
        # We have at least SOME fresh data to push. Always run write_raw_sheets;
        # the underlying upsert is idempotent and includes anything currently
        # on disk (which may include unchanged inputs that still need to be
        # mirrored on a cold-start day).
        ok, _ = run_step(
            "write_raw_sheets",
            lambda: subprocess.run(
                [sys.executable, "-m", "agents.bhaga.scripts.backfill_from_downloads",
                 "--store", args.store],
                cwd=str(PROJECT_ROOT), check=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            ),
            refresh_date=refresh_date.isoformat(),
            dry_run=args.dry_run,
        )
        if ok:
            raw_sheets_ok = True
        else:
            failures.append(("write_raw_sheets", RuntimeError("see step log")))
    else:
        print("[write_raw_sheets] SKIPPED — no fresh inputs to mirror.")

    # Step 4: Refresh Model sheet. Now reads from the raw sheets (post-refactor)
    # rather than from local files. Requires raw_sheets_ok unless --skip-square
    # AND --skip-timecard both set (manual re-derive from existing raw data).
    failed_steps = {name for name, _ in failures}
    if not args.skip_model and (raw_sheets_ok or (args.skip_square and args.skip_timecard)):
        ok, val = run_step(
            "update_model_sheet",
            lambda: subprocess.run(
                [sys.executable, "-m", "agents.bhaga.scripts.update_model_sheet",
                 "--store", args.store],
                cwd=str(PROJECT_ROOT), check=True,
            ),
            refresh_date=refresh_date.isoformat(),
            dry_run=args.dry_run,
        )
        if not ok:
            failures.append(("update_model_sheet", val))

    # Step 5: Google Review bonuses. Runs AFTER the model refresh so that
    # process_reviews can pull punches from the freshly-mirrored
    # `bhaga_adp_raw > punches` tab (architecture contract: never local files).
    # Idempotent — uses `BHAGA Review Raw > config.last_processed_ts_ms` as the
    # high-water mark, so a no-op rerun is safe. Cheap to skip with
    # --skip-reviews if ClickUp is down.
    if not args.skip_reviews and raw_sheets_ok:
        ok, val = run_step(
            "process_reviews",
            lambda: subprocess.run(
                [sys.executable, "-m", "agents.bhaga.scripts.process_reviews",
                 "--store", args.store]
                + (["--no-slack"] if args.no_slack else []),
                cwd=str(PROJECT_ROOT), check=True,
            ),
            refresh_date=refresh_date.isoformat(),
            dry_run=args.dry_run,
        )
        if not ok:
            # Reviews failing is non-fatal for tips — log and continue.
            failures.append(("process_reviews", val))
    elif args.skip_reviews:
        print("[process_reviews] SKIPPED — --skip-reviews flag set.")
    else:
        print("[process_reviews] SKIPPED — raw_sheets_ok=False (need fresh ADP punches).")

    runtime_s = time.monotonic() - t_start

    if failures:
        names = ", ".join(name for name, _ in failures)
        print(f"\n=== {len(failures)} step(s) failed: {names} ===")
        # failure_alert was already called per-step. Don't double-DM.
        return 1

    print(f"\n=== DONE in {runtime_s:.1f}s ===")
    if not args.dry_run:
        success_heartbeat(
            date=refresh_date.isoformat(),
            tabs_written=6,  # 5 model tabs + review_bonus_period
            runtime_s=runtime_s,
            extra=(
                f"Square gap: {gap_start.isoformat()} → {refresh_date.isoformat()}"
                + (f" ({master_stats.get('rows_added', 0)} new rows, "
                   f"master now {master_stats.get('master_rows', 0)})" if master_stats
                   else " (no new days)")
                + "\n"
                f"ADP Timecard: {artifacts['adp_timecard_xlsx'].name if artifacts['adp_timecard_xlsx'] else '(skipped)'}\n"
                f"ADP Earnings: {artifacts['adp_earnings_xlsx'].name if artifacts['adp_earnings_xlsx'] else '(skipped — not Mon/Tue)'}"
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
