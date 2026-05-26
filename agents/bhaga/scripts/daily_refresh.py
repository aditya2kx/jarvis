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
    7. Run update_model_sheet to refresh the 8 Model workbook tabs:
       config, daily, labor_daily, labor_weekly, labor_period,
       tip_alloc_period, tip_alloc_daily, period_summary.
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
from agents.bhaga.scripts.gcs_cache import (
    download_cached_files,
    upload_scrape_artifacts,
)
from core.config_loader import refresh_access_token, resolve_sheet_id
from skills.adp_run_automation.runner import download_adp_bundle
from skills.bhaga_config.dates import coerce_iso_date
from skills.bhaga_config.state_adapter import (
    mark_step_done as _adapter_mark_step_done,
    run_state_dir as _adapter_run_state_dir,
    step_already_done as _adapter_step_already_done,
)
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
) -> tuple[datetime.date | None, bool]:
    """Read the `data_window_end` value from the Model sheet's config tab.

    Returns ``(prev_end, cell_was_empty)``:
      - ``prev_end`` is the parsed ``datetime.date`` when the cell holds a
        valid ISO date, an apostrophe-prefixed ISO date, or a Sheets
        date-serial that recovers cleanly via ``coerce_iso_date``.
      - ``prev_end`` is ``None`` when the cell is either empty OR genuinely
        unparseable; the second tuple slot ``cell_was_empty`` disambiguates
        these two cases so Layer C (compute_gap_window) can fresh-install
        in the empty branch but hard-error in the unparseable branch
        (we refuse to silently trigger a 60-day Square re-scrape).
      - Both ``None, True`` is also returned when the entire config tab
        is unreadable (network error, missing tab) — treated as fresh
        install for the legacy code path.
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
        return None, True
    for row in data.get("values", []):
        if row and row[0] == "data_window_end":
            raw = row[1] if len(row) >= 2 else ""
            iso = coerce_iso_date(raw)
            if iso is not None:
                return datetime.date.fromisoformat(iso), False
            # The key exists. Distinguish "empty cell" from
            # "cell holds something we can't parse".
            cell_was_empty = not (
                isinstance(raw, str) and raw.strip().lstrip("'").strip()
            )
            if not cell_was_empty:
                print(
                    f"  [config-read] data_window_end has unparseable value: {raw!r}"
                )
            return None, cell_was_empty
    return None, True


def compute_gap_window(
    prev_end: datetime.date | None,
    cell_was_empty: bool,
    data_start: datetime.date,
    refresh_date: datetime.date,
) -> tuple[datetime.date, str]:
    """Decide what date range Square needs to scrape this run.

    Pure function — extracted from ``main()`` so it can be unit-tested
    without standing up Sheets/Playwright/Slack side effects.

    Returns ``(gap_start, gap_source)``. ``gap_source`` is a short
    human-readable label that lands in the refresh log so operators
    can tell at a glance which branch fired.

    Branches:
      - ``prev_end`` set → incremental: gap_start = prev_end + 1 day.
      - ``prev_end`` is None AND cell_was_empty → fresh install: scrape
        from ``data_start`` (the store profile's first-data-window).
      - ``prev_end`` is None AND NOT cell_was_empty → the config cell
        holds something that even ``coerce_iso_date`` can't recover.
        Hard-error rather than silently fall back to a 60-day Square
        re-scrape (which costs the API budget AND a fresh 2FA round-
        trip). See ``seamless_bhaga_refresh`` Layer C for the
        rationale.

    Raises ``SystemExit`` on the unparseable+non-empty case. The
    message contains the literal phrase
    ``"60-day Square re-scrape"`` so a regression test can grep it.
    """
    if prev_end is not None:
        return prev_end + datetime.timedelta(days=1), (
            f"sheet.data_window_end={prev_end} + 1"
        )
    if cell_was_empty:
        return data_start, f"fresh install -> data_start={data_start}"
    raise SystemExit(
        "[daily_refresh] FATAL: bhaga_model > config.data_window_end "
        "is set but unparseable. Refusing to fall back to fresh-install "
        "because that would trigger a 60-day Square re-scrape + fresh 2FA. "
        "Inspect the config cell (it's likely a date-serial integer like "
        "'46162' — coerce_iso_date should normally recover it, so this "
        "is true junk) and rerun."
    )


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


# ── Per-day step markers (Layer B idempotency) ─────────────────────
#
# When the wrapper fires and a step partially succeeds, we don't want the
# next manual --force re-run (or recovery attempt) to redo the already-done
# work. Each step writes a success marker to
#   ~/.bhaga/state/run-{refresh_date}/{step_name}.done
# and run_step short-circuits on entry if its marker is present.
#
# IMPORTANT: the marker dir is keyed by REFRESH_DATE (the business date
# whose data we're publishing), NOT by today_ct (wall-clock CT date). This
# matters for recovery runs — if at 13:20 CT on 5/21 an operator invokes
# `--date 2026-05-20` to retry yesterday's failure, the markers must land
# under run-2026-05-20/ so tonight's 21:00 CT cron (which runs --date
# 2026-05-21) starts with a fresh marker set. The previous keying-by-
# today_ct caused exactly this collision: the 5/20 recovery wrote markers
# under run-2026-05-21/ and the 21:00 CT cron then short-circuited.
#
# Layer A (file-based, in download_* functions) handles the cron-storm case:
# if the CSV/XLSX is on disk for today, skip the browser entirely.
# Layer B (here) handles non-download steps and any cross-process recovery
# the operator does. Cleanup: dirs older than 7 days are pruned at wrapper
# start.


def _run_state_dir(refresh_date: datetime.date) -> pathlib.Path:
    return _adapter_run_state_dir(refresh_date)


def step_already_done(refresh_date: datetime.date, step_name: str) -> bool:
    return _adapter_step_already_done(refresh_date, step_name)


def mark_step_done(refresh_date: datetime.date, step_name: str, *, note: str = "") -> None:
    _adapter_mark_step_done(refresh_date, step_name, note=note)


# Cron triggers at 21:00 CT; the shop closes by ~20:00 CT so the +1h buffer
# guarantees end-of-day Square + ADP data is settled before we touch it.
# Tune by editing this constant — see is_refresh_date_complete().
_SHOP_CLOSE_BUFFER_HOUR_CT = 21


def is_refresh_date_complete(
    refresh_date: datetime.date,
    *,
    now_ct: datetime.datetime | None = None,
) -> bool:
    """Return True iff refresh_date's data sources are expected to be complete.

    A date X is "complete" when:
      - X < today_ct (any past calendar day in CT), OR
      - X == today_ct AND now_ct.hour >= _SHOP_CLOSE_BUFFER_HOUR_CT

    Otherwise X is in-progress (today before 21:00 CT) or in the future.

    Pure function; tests pass a fixed ``now_ct`` to stay deterministic.
    """
    if now_ct is None:
        now_ct = datetime.datetime.now(CT)
    today_ct = now_ct.date()
    if refresh_date < today_ct:
        return True
    if refresh_date > today_ct:
        return False
    return now_ct.hour >= _SHOP_CLOSE_BUFFER_HOUR_CT


def cleanup_old_run_dirs(*, keep_days: int = 7) -> None:
    """Prune ~/.bhaga/state/run-YYYY-MM-DD/ dirs older than keep_days.

    Safe no-op if the parent dir doesn't exist or no dirs match.
    """
    parent = pathlib.Path.home() / ".bhaga" / "state"
    if not parent.is_dir():
        return
    today = datetime.datetime.now(CT).date()
    cutoff = today - datetime.timedelta(days=keep_days)
    for child in parent.iterdir():
        if not child.is_dir() or not child.name.startswith("run-"):
            continue
        try:
            d = datetime.date.fromisoformat(child.name[len("run-"):])
        except ValueError:
            continue
        if d < cutoff:
            try:
                for f in child.iterdir():
                    f.unlink(missing_ok=True)
                child.rmdir()
                print(f"[cleanup] pruned old run dir: {child}")
            except Exception as exc:  # noqa: BLE001
                print(f"[cleanup] could not prune {child}: {exc}")


def _assert_master_not_older_than_gap(
    *, master_csv: pathlib.Path, gap_csv: pathlib.Path | None,
) -> None:
    """Pre-flight check before write_raw_sheets: master_csv mtime must be
    at least as new as the gap_csv mtime.

    If consolidate_csv ran successfully, master_csv was rewritten AFTER
    gap_csv was downloaded, so master_csv.mtime >= gap_csv.mtime. If this
    invariant is violated, something silently failed to merge the gap rows
    into the master and write_raw_sheets is about to ship stale data to the
    raw sheets. Fail LOUDLY rather than completing with exit 0.

    This is one of the two defenses added on 2026-05-23 against the
    "silent partial-success" class of bugs (the other is the post-condition
    guard in main() that re-reads data_window_end). Both check different
    things; both are needed.
    """
    if gap_csv is None or not gap_csv.exists():
        return
    if not master_csv.exists():
        raise RuntimeError(
            f"[write_raw_sheets] precondition violated: gap CSV exists "
            f"({gap_csv.name}) but master CSV does not ({master_csv.name}). "
            f"consolidate_csv must have failed silently — refusing to write "
            f"raw sheets from an incomplete master."
        )
    gap_mtime = gap_csv.stat().st_mtime
    master_mtime = master_csv.stat().st_mtime
    if master_mtime < gap_mtime:
        raise RuntimeError(
            f"[write_raw_sheets] precondition violated: master CSV "
            f"({master_csv.name}, mtime={master_mtime}) is OLDER than the gap "
            f"CSV ({gap_csv.name}, mtime={gap_mtime}). consolidate_csv did "
            f"not rewrite the master after the gap was downloaded — "
            f"refusing to write raw sheets from stale master."
        )


def _assert_data_advanced_post_condition(
    *,
    prev_end: datetime.date | None,
    post_end: datetime.date | None,
    rows_added_from_gap: int,
    update_model_ran: bool,
    refresh_date: datetime.date,
) -> None:
    """Final guard against the 2026-05-23 silent partial-success class.

    Fires AFTER all steps complete but BEFORE the orchestrator declares
    success. Catches the case where:
      * a non-empty Square gap was merged into the master CSV
      * update_model_sheet ran
      * but ``bhaga_model > config.data_window_end`` did NOT advance past
        ``prev_end``.

    That combination means write_raw_sheets or update_model_sheet silently
    swallowed the new data — exactly the failure mode that produced the
    2026-05-23 incident (parse_csv dropped all Asia/Calcutta-tz rows
    because the operator was traveling in India, so 189 fresh rows reached
    the master file but zero made it to the raw sheets). Without this
    guard the orchestrator exits 0, writes `.done` markers, and the
    operator loses 12+ hours before noticing.

    Pure function so the contract can be unit-tested without standing up
    Sheets / Playwright. Raises RuntimeError on violation.
    """
    if not update_model_ran:
        return
    if prev_end is None:
        # Fresh install or unreadable config — separate failure mode.
        return
    if rows_added_from_gap <= 0:
        # No new data was supposed to land; not advancing is correct.
        return
    if post_end is None:
        raise RuntimeError(
            f"silent partial-success guard: {rows_added_from_gap} new Square "
            f"row(s) were merged into the master CSV for refresh_date="
            f"{refresh_date.isoformat()}, but data_window_end could not be "
            f"re-read from bhaga_model > config after update_model_sheet. "
            f"Cannot verify the data made it through — refusing to declare "
            f"success."
        )
    if post_end <= prev_end:
        raise RuntimeError(
            f"silent partial-success guard: {rows_added_from_gap} new Square "
            f"row(s) merged into master.csv for refresh_date="
            f"{refresh_date.isoformat()}, but bhaga_model > config."
            f"data_window_end did NOT advance past {prev_end.isoformat()} "
            f"(post-run value={post_end.isoformat()}). This means "
            f"write_raw_sheets or update_model_sheet silently dropped the "
            f"new rows. Inspect the raw_square_transactions tab vs the "
            f"master CSV before retrying."
        )


def _adp_bundle_then_raise(
    *,
    store: str,
    target_date: datetime.date,
    include_earnings: bool,
    headed: bool,
) -> dict:
    """Wrap download_adp_bundle so the orchestrator's run_step sees a clean
    success/exception contract.

    The bundle deliberately swallows per-component exceptions so that a
    Timecard failure doesn't prevent Earnings from running (and vice versa).
    Once BOTH attempts have completed and per-component markers + screenshots
    have been written, this wrapper inspects `result["errors"]` and raises a
    summary RuntimeError if anything failed. By this point the partial
    success is durable on disk and run-state markers; the exception just
    surfaces it to the orchestrator + Slack alert path.
    """
    result = download_adp_bundle(
        store=store,
        target_date=target_date,
        include_earnings=include_earnings,
        headed=headed,
    )
    errs = result.get("errors") or {}
    if errs:
        summary = "; ".join(f"{name}: {msg}" for name, msg in errs.items())
        raise RuntimeError(f"adp_bundle partial failure ({len(errs)} component(s)): {summary}")
    return result


def run_step(
    step_name: str,
    fn,
    *,
    refresh_date: datetime.date,
    dry_run: bool,
) -> tuple[bool, object]:
    """Run a step; on exception, send Slack failure_alert and return (False, exc).

    Idempotency: if the step's success marker already exists in the
    refresh_date's run state dir, skip execution entirely. Use
    --force-step (TODO) or delete the marker file to force a re-run.

    ``refresh_date`` is the BUSINESS date being published (not today_ct);
    markers are keyed off it so a recovery run for a past date never
    collides with the upcoming nightly cron's marker namespace.

    Returns (success, return_value_or_exception)."""
    if step_already_done(refresh_date, step_name) and not dry_run:
        print(f"\n[{step_name}] SKIPPED — already completed for "
              f"refresh_date={refresh_date.isoformat()} (marker: "
              f"{_run_state_dir(refresh_date) / f'{step_name}.done'})")
        return True, None
    print(f"\n[{step_name}] starting...")
    t0 = time.monotonic()
    if dry_run:
        print(f"[{step_name}] DRY RUN — skipped.")
        return True, None
    try:
        result = fn()
        dt = time.monotonic() - t0
        print(f"[{step_name}] OK ({dt:.1f}s) -> {result}")
        try:
            mark_step_done(
                refresh_date, step_name,
                note=f"runtime={dt:.1f}s, refresh_date={refresh_date.isoformat()}",
            )
        except Exception as mark_exc:  # noqa: BLE001
            print(f"[{step_name}] WARN: could not write step marker: {mark_exc}")
        return True, result
    except Exception as exc:  # noqa: BLE001
        dt = time.monotonic() - t0
        print(f"[{step_name}] FAILED after {dt:.1f}s: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            failure_alert(
                step=step_name,
                exception=exc,
                date=refresh_date.isoformat(),
                extra=(
                    f"This step failed after {dt:.1f}s. With strict-1-attempt "
                    "enabled, the wrapper writes the day marker and stops. "
                    "Re-run manually: python3 -m agents.bhaga.scripts.daily_refresh_wrapper --force "
                    f"(steps already completed for refresh_date will skip via marker in "
                    f"{_run_state_dir(refresh_date)})."
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

    # ── Completeness gate ────────────────────────────────────────────
    # Refuse to run for a refresh_date whose data sources are still in
    # flight (today before 21:00 CT, or any future date). Without this
    # gate, an operator who runs `--date <past>` at 13:00 CT to recover
    # from yesterday's failure would still trigger a partial today-pull
    # AND write markers under run-<today_ct>/ that block the nightly
    # cron. See the marker-dir refactor above for the other half of the
    # fix. Place BEFORE any data fetches / step runs / marker writes.
    if not is_refresh_date_complete(refresh_date):
        now_ct = datetime.datetime.now(CT)
        raise SystemExit(
            f"ERROR: refresh_date={refresh_date.isoformat()} is not yet complete.\n"
            f"  Now (CT): {now_ct.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"  Required: today_ct > refresh_date OR "
            f"(today_ct == refresh_date AND hour >= "
            f"{_SHOP_CLOSE_BUFFER_HOUR_CT}:00 CT)\n"
            f"  Fix: wait until {_SHOP_CLOSE_BUFFER_HOUR_CT}:00 CT for today's "
            f"run, or pass --date <past-date> for backfill."
        )

    profile = _load_profile(args.store)
    data_start = datetime.date.fromisoformat(profile["calibration"]["first_data_window"]["start"])
    spreadsheet_id = resolve_sheet_id("bhaga_model", profile)

    # ---- Incremental window resolution ----------------------------------
    # Source of truth: Model sheet's config tab `data_window_end`.
    # gap = [data_window_end + 1, refresh_date]
    # If the sheet has no entry yet, this is a fresh install -> full backfill
    # from `data_start`. After that first run, all subsequent runs are
    # strictly incremental.
    # `prev_end` is captured for the post-condition guard at the end of
    # main(); we want the SAME value the gap window was computed against,
    # so we set it unconditionally here (None in the --from-date /
    # --skip-square branches where there's nothing to compare against).
    prev_end: datetime.date | None = None
    if args.from_date:
        gap_start = datetime.date.fromisoformat(args.from_date)
        gap_source = "--from-date override"
    elif args.skip_square:
        gap_start = refresh_date
        gap_source = "(square skipped)"
    else:
        prev_end, cell_was_empty = _read_data_window_end_from_sheet(
            spreadsheet_id=spreadsheet_id, store=args.store
        )
        gap_start, gap_source = compute_gap_window(
            prev_end=prev_end,
            cell_was_empty=cell_was_empty,
            data_start=data_start,
            refresh_date=refresh_date,
        )

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
            refresh_date=refresh_date,
            dry_run=args.dry_run,
        )
        if ok:
            artifacts["square_csv"] = val
            if val is not None and not args.dry_run:
                ok2, val2 = run_step(
                    "consolidate_csv",
                    lambda: _consolidate_into_master(gap_csv=val),
                    refresh_date=refresh_date,
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

    # ── GCS cache: upload Square artifacts after scrape + consolidate ──
    if not args.dry_run and "square_transactions" not in {n for n, _ in failures}:
        try:
            upload_scrape_artifacts(
                refresh_date=refresh_date,
                download_dir=DOWNLOAD_DIR,
                square_csv=artifacts.get("square_csv") if isinstance(artifacts.get("square_csv"), pathlib.Path) else None,
                master_csv=MASTER_TXN_CSV if MASTER_TXN_CSV.exists() else None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [gcs_cache] WARN: Square upload failed (non-fatal): {exc}")

    # Step 2 + 3 (combined): ADP Reports bundle.
    # Both Timecard and Earnings now run in a SINGLE browser session via
    # download_adp_bundle — one login, at most one OTP cost per nightly run.
    # The bundle returns a partial-success dict (timecard_xlsx / earnings_xlsx
    # / errors); per-component .done markers are written inside the bundle so
    # operator-facing granularity is preserved. We raise here AFTER both
    # attempts have run so the partial success lands on disk + in markers
    # before the exception propagates.
    if not args.skip_timecard:
        ok, val = run_step(
            "adp_reports",
            lambda: _adp_bundle_then_raise(
                store=args.store,
                target_date=refresh_date,
                include_earnings=include_rates,
                headed=headed,
            ),
            refresh_date=refresh_date,
            dry_run=args.dry_run,
        )
        if ok and isinstance(val, dict):
            artifacts["adp_timecard_xlsx"] = val.get("timecard_xlsx")
            artifacts["adp_earnings_xlsx"] = val.get("earnings_xlsx")
        elif not ok:
            # The bundle raised (one or both components failed). Translate
            # into the legacy failure list shape so downstream gating logic
            # (square_ok / raw_sheets_ok) keeps working unchanged.
            failures.append(("adp_reports", val))

    # ── GCS cache: upload ADP artifacts after scrape ──
    if not args.dry_run and "adp_reports" not in {n for n, _ in failures}:
        try:
            upload_scrape_artifacts(
                refresh_date=refresh_date,
                download_dir=DOWNLOAD_DIR,
                adp_timecard_xlsx=artifacts.get("adp_timecard_xlsx") if isinstance(artifacts.get("adp_timecard_xlsx"), pathlib.Path) else None,
                adp_earnings_xlsx=artifacts.get("adp_earnings_xlsx") if isinstance(artifacts.get("adp_earnings_xlsx"), pathlib.Path) else None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [gcs_cache] WARN: ADP upload failed (non-fatal): {exc}")

    # ── GCS cache: restore missing files before downstream steps ──
    # On Cloud Run re-runs, scrape markers say "done" but ephemeral FS is
    # empty. Pull from GCS so write_raw_sheets/update_model_sheet proceed
    # without re-scraping (no OTP cost).
    if not args.dry_run:
        critical_missing = (
            not MASTER_TXN_CSV.exists()
            or (not args.skip_timecard and not any(DOWNLOAD_DIR.glob("Timecard-*.xlsx")))
        )
        if critical_missing:
            print("\n[gcs_cache] local files missing — attempting restore from GCS cache...")
            try:
                restored = download_cached_files(
                    refresh_date=refresh_date,
                    download_dir=DOWNLOAD_DIR,
                )
                if restored:
                    print(f"  [gcs_cache] restored {len(restored)} file(s) from GCS")
                else:
                    print("  [gcs_cache] no cached files found in GCS for this date")
            except Exception as exc:  # noqa: BLE001
                print(f"  [gcs_cache] WARN: GCS restore failed (non-fatal): {exc}")

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
        gap_csv_for_check = artifacts.get("square_csv") if isinstance(artifacts.get("square_csv"), pathlib.Path) else None

        def _write_raw_sheets_step():
            # Pre-flight: master.csv must not be older than the gap CSV. If it
            # is, consolidate_csv silently failed to merge — abort BEFORE
            # shipping stale data to raw sheets. See 2026-05-23 incident.
            _assert_master_not_older_than_gap(
                master_csv=MASTER_TXN_CSV, gap_csv=gap_csv_for_check,
            )
            return subprocess.run(
                [sys.executable, "-m", "agents.bhaga.scripts.backfill_from_downloads",
                 "--store", args.store],
                cwd=str(PROJECT_ROOT), check=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )

        ok, _ = run_step(
            "write_raw_sheets",
            _write_raw_sheets_step,
            refresh_date=refresh_date,
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
            refresh_date=refresh_date,
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
            refresh_date=refresh_date,
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

    # ── Post-condition guard: did the new data actually land? ──────
    # Re-read bhaga_model > config.data_window_end and compare against
    # the pre-run value. If a non-empty gap was supposed to advance the
    # window but didn't, something silently swallowed the rows
    # (2026-05-23 incident). Fail loudly BEFORE writing the success
    # heartbeat — the wrapper will retry on the next 15-min wakeup.
    failed_step_names = {name for name, _ in failures}
    update_model_ran = (
        not args.skip_model
        and "update_model_sheet" not in failed_step_names
        and not args.dry_run
        # If --skip-square AND --skip-timecard both set, model is being
        # re-derived from existing raw data; data_window_end may legitimately
        # stay put. The rows_added_from_gap check below also covers this.
    )
    post_end: datetime.date | None = None
    if update_model_ran and not args.skip_square:
        try:
            post_end, _ = _read_data_window_end_from_sheet(
                spreadsheet_id=spreadsheet_id, store=args.store
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [post-condition] could not re-read data_window_end: {exc}")
            post_end = None
    try:
        _assert_data_advanced_post_condition(
            prev_end=prev_end,
            post_end=post_end,
            rows_added_from_gap=master_stats.get("rows_added", 0),
            update_model_ran=update_model_ran,
            refresh_date=refresh_date,
        )
    except RuntimeError as exc:
        print(f"\n!!! POST-CONDITION GUARD FAILED: {exc}", file=sys.stderr)
        try:
            failure_alert(
                step="post_condition_guard",
                exception=exc,
                date=refresh_date.isoformat(),
                extra=(
                    "The daily refresh completed every step's marker but the "
                    "Model sheet's data_window_end did not advance despite new "
                    "Square rows merging into the master CSV. This is the "
                    "2026-05-23 silent-partial-success class — investigate "
                    "write_raw_sheets / update_model_sheet before retrying. "
                    "To force a retry, delete ~/.bhaga/state/run-"
                    f"{refresh_date.isoformat()}/write_raw_sheets.done and "
                    f"~/.bhaga/state/run-{refresh_date.isoformat()}/"
                    "update_model_sheet.done."
                ),
            )
        except Exception:  # noqa: BLE001, S110
            pass
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
