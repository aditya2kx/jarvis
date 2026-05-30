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
import concurrent.futures
import csv
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

import functools

from agents.bhaga.notify import (
    failure_alert,
    info_ping,
    otp_skipped_alert,
    ready_request,
    success_heartbeat,
)
from agents.bhaga.scripts import otp_gate
from agents.bhaga.scripts.gcs_cache import (
    download_cached_files,
    upload_scrape_artifacts,
)
from core.config_loader import refresh_access_token, resolve_sheet_id
from skills.bhaga_config.dates import coerce_iso_date
from skills.bhaga_config.state_adapter import (
    clear_pending_otp as _adapter_clear_pending_otp,
    mark_step_done as _adapter_mark_step_done,
    run_state_dir as _adapter_run_state_dir,
    save_pending_otp as _adapter_save_pending_otp,
    step_already_done as _adapter_step_already_done,
)
# NOTE: Square/ADP scrape + browser imports are intentionally LAZY (inside the
# functions that scrape) so that importing daily_refresh — e.g. for its pure
# verification contract (MODEL_VERIFY_MIN_ROWS, assert_model_tabs_populated,
# is_refresh_date_complete) — never pulls in patchright or any login/OTP code.
# This is what lets the sandbox e2e runner compose update_model_sheet without a
# scrape module ever entering its import graph.

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


# ── Model-sheet verification (built into the pipeline) ─────────────
#
# After update_model_sheet rebuilds the Model workbook, read it back and
# assert the expected tabs are non-empty for the data window. This turns
# "the period tabs are silently empty" (the 2026-05-27 bug) into a loud,
# alerting failure that the operator sees the same night instead of
# discovering days later. The assertion logic is a pure function
# (assert_model_tabs_populated) so it's unit-testable without Sheets.

# Tabs that MUST have >= this many data rows after a full model rebuild.
# labor_period / period_summary expect >= 1 because the data window always
# spans multiple biweekly pay periods (so at least one period + one open
# period exist). daily / labor_daily / labor_weekly / labor_daily_forecast
# expect >= 1 because the window always covers >= 1 complete day.
MODEL_VERIFY_MIN_ROWS: dict[str, int] = {
    "daily": 1,
    "labor_daily": 1,
    "labor_weekly": 1,
    "labor_period": 1,
    "labor_daily_forecast": 1,
    "period_summary": 1,
}

# Header used to confirm KDS columns made it into the model's labor tabs.
_KDS_MODEL_COLUMN_HEADER = "kds_completed_tickets"

# Per-item / over-goal / late KDS metrics that MUST populate at weekly + period
# grain for rows overlapping KDS coverage. These pool the per-day item
# distributions; the verification guards against the join silently blanking
# them. (avg_time_per_item_sec was removed — percentiles + median replace it.)
_KDS_PERIODIC_METRIC_HEADERS = (
    "kds_median_time_per_item_sec",
    "kds_p90_time_per_item_sec",
    "kds_p95_time_per_item_sec",
    "kds_p99_time_per_item_sec",
    "kds_pct_items_over_goal",
    "kds_pct_tickets_late",
)
# Date-boundary column names per tab, used to test KDS-coverage overlap.
_PERIODIC_DATE_COLS = {
    "labor_weekly": ("week_start", "week_end"),
    "labor_period": ("pay_period_start", "pay_period_end"),
}


def _parse_sheet_iso_date(cell: object) -> str | None:
    """Normalize a sheet date cell to a plain ISO ``YYYY-MM-DD`` string.

    Model-sheet date cells are written apostrophe-prefixed as text literals
    (e.g. ``'2026-05-04``); read back via the values API they come through
    without the apostrophe but we strip it defensively. Returns None if the
    first 10 chars aren't a parseable ISO date.
    """
    s = str(cell or "").strip().lstrip("'").strip()
    if len(s) < 10:
        return None
    candidate = s[:10]
    try:
        datetime.date.fromisoformat(candidate)
    except (ValueError, TypeError):
        return None
    return candidate


def check_weekly_period_kds(
    *,
    weekly_values: list[list] | None,
    period_values: list[list] | None,
    kds_min_date: str | None,
    kds_max_date: str | None,
) -> list[str]:
    """Pure check: weekly/period rows overlapping KDS coverage must carry the
    three per-item/late KDS metrics (Workstream A).

    For each row whose [start, end] window overlaps the KDS-covered date range
    ``[kds_min_date, kds_max_date]`` AND that already shows KDS throughput
    (``kds_completed_items`` non-empty), assert that every metric in
    ``_KDS_PERIODIC_METRIC_HEADERS`` (median / p90 / p95 / p99 /
    pct_items_over_goal / pct_tickets_late) is non-empty. This guards against
    the join silently hard-blanking those columns at weekly/period grain.

    Rows entirely BEFORE KDS coverage (KDS started 2026-04-24) have empty
    throughput and don't overlap, so they're skipped — no false positives.

    Also asserts that at least ONE overlapping row was actually checked in each
    tab (otherwise the whole KDS→weekly/period join silently produced nothing).

    Pure function (no I/O) so it's unit-testable without Sheets. Returns a list
    of problem strings (empty when healthy). When ``kds_min_date`` /
    ``kds_max_date`` are None (coverage unknown), returns [] (treated as
    "can't check", not "failed").
    """
    if not kds_min_date or not kds_max_date:
        return []
    problems: list[str] = []
    for tab, values in (("labor_weekly", weekly_values), ("labor_period", period_values)):
        if not values:
            problems.append(f"{tab}: tab unreadable for weekly/period KDS check")
            continue
        header = values[0]

        def _idx(name: str) -> int:
            try:
                return header.index(name)
            except ValueError:
                return -1

        start_name, end_name = _PERIODIC_DATE_COLS[tab]
        si, ei = _idx(start_name), _idx(end_name)
        items_i = _idx(_KDS_MODEL_COLUMN_HEADER.replace("tickets", "items"))  # kds_completed_items
        metric_idx = {m: _idx(m) for m in _KDS_PERIODIC_METRIC_HEADERS}
        missing_cols = [
            n for n, i in (
                [(start_name, si), (end_name, ei), ("kds_completed_items", items_i)]
                + [(m, metric_idx[m]) for m in _KDS_PERIODIC_METRIC_HEADERS]
            ) if i < 0
        ]
        if missing_cols:
            problems.append(f"{tab}: missing expected columns {missing_cols}")
            continue

        checked = 0
        for row in values[1:]:
            def _cell(i: int) -> str:
                return str(row[i]).strip() if 0 <= i < len(row) else ""

            start = _parse_sheet_iso_date(_cell(si))
            end = _parse_sheet_iso_date(_cell(ei))
            if not start or not end:
                continue
            overlaps = end >= kds_min_date and start <= kds_max_date
            has_items = _cell(items_i) != ""
            if not (overlaps and has_items):
                continue
            checked += 1
            empties = [m for m in _KDS_PERIODIC_METRIC_HEADERS if _cell(metric_idx[m]) == ""]
            if empties:
                problems.append(
                    f"{tab}: row {start}..{end} has KDS items but empty "
                    f"{', '.join(empties)}"
                )
        if checked == 0:
            problems.append(
                f"{tab}: no KDS-overlapping rows had populated metrics "
                f"(expected weekly/period KDS to populate for dates within "
                f"{kds_min_date}..{kds_max_date})"
            )
    return problems


def assert_model_tabs_populated(
    *,
    tab_row_counts: dict[str, int],
    expect_kds: bool,
    raw_kds_row_count: int | None = None,
    model_kds_columns_nonempty: bool | None = None,
    weekly_values: list[list] | None = None,
    period_values: list[list] | None = None,
    kds_min_date: str | None = None,
    kds_max_date: str | None = None,
    min_rows: dict[str, int] | None = None,
) -> None:
    """Pure guard: raise RuntimeError if the rebuilt model is under-populated.

    Args:
        tab_row_counts: {tab_name: data_row_count} read back from the model.
        expect_kds: True when KDS data was scraped this run (no --skip-kds),
            so the KDS-specific assertions apply.
        raw_kds_row_count: data rows in the raw square `kds_daily` tab, or
            None if not read. Only consulted when ``expect_kds``.
        model_kds_columns_nonempty: True/False whether the model's
            labor_daily KDS columns have at least one non-empty value, or
            None if not read. Only consulted when ``expect_kds``.
        weekly_values / period_values: full labor_weekly / labor_period grids
            (header + rows) read back from the model, or None if not read.
            Only consulted when ``expect_kds`` together with kds_min/max.
        kds_min_date / kds_max_date: ISO bounds of KDS-covered dates (from the
            raw kds_daily tab), or None when coverage is unknown. Used to scope
            the weekly/period KDS assertion to overlapping rows only.
        min_rows: override of MODEL_VERIFY_MIN_ROWS (for tests).

    Pure function — no I/O — so the contract can be unit-tested without
    standing up Sheets. Mirrors _assert_data_advanced_post_condition's style.
    """
    expectations = min_rows or MODEL_VERIFY_MIN_ROWS
    problems: list[str] = []
    for tab, minimum in sorted(expectations.items()):
        n = tab_row_counts.get(tab)
        if n is None:
            problems.append(f"{tab}: tab missing/unreadable (expected >= {minimum})")
        elif n < minimum:
            problems.append(f"{tab}: {n} row(s) (expected >= {minimum})")
    if expect_kds:
        if raw_kds_row_count is not None and raw_kds_row_count <= 0:
            problems.append(
                "kds_daily (raw square sheet): 0 rows but KDS was expected "
                "(no --skip-kds) — KDS scrape/backfill did not land"
            )
        if model_kds_columns_nonempty is False:
            problems.append(
                "labor_daily: KDS columns are entirely empty but KDS data "
                "was expected — model did not join KDS into labor tabs"
            )
        # Weekly + period grain KDS metrics (Workstream A regression guard).
        # Only runs when we actually read the grids back AND know KDS coverage;
        # otherwise it's a no-op (coverage unknown → can't false-positive).
        if weekly_values is not None or period_values is not None:
            problems.extend(
                check_weekly_period_kds(
                    weekly_values=weekly_values,
                    period_values=period_values,
                    kds_min_date=kds_min_date,
                    kds_max_date=kds_max_date,
                )
            )
    if problems:
        raise RuntimeError(
            "model-sheet verification failed: " + "; ".join(problems)
        )


def _sheets_batch_get(
    spreadsheet_id: str, token: str, ranges: list[str]
) -> dict:
    qs = "&".join(
        f"ranges={urllib.parse.quote(r, safe='!:')}" for r in ranges
    )
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/"
        f"{spreadsheet_id}/values:batchGet?{qs}&majorDimension=ROWS"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _labor_daily_has_kds(values: list[list]) -> bool:
    """True iff the labor_daily matrix has >= 1 non-empty KDS-tickets cell."""
    if not values:
        return False
    header = values[0]
    try:
        idx = header.index(_KDS_MODEL_COLUMN_HEADER)
    except ValueError:
        return False
    for row in values[1:]:
        if len(row) > idx and str(row[idx]).strip():
            return True
    return False


def _read_model_verification_data(
    *,
    spreadsheet_id: str,
    store: str,
    raw_square_sid: str,
    expect_kds: bool,
) -> dict:
    """Read back the model (and raw KDS) for verification.

    Returns a dict with keys:
        tab_row_counts            : {tab_name: data_row_count}
        model_kds_columns_nonempty: bool | None (labor_daily KDS join present)
        raw_kds_row_count         : int | None (raw kds_daily data rows)
        weekly_values             : list[list] | None (labor_weekly grid)
        period_values             : list[list] | None (labor_period grid)
        kds_min_date / kds_max_date: ISO bounds of KDS-covered dates, or None

    All KDS-specific values are None when ``expect_kds`` is False or the source
    can't be read (treated as "unknown", not "failed", by the assertion).
    """
    token = refresh_access_token(store)
    tabs = list(MODEL_VERIFY_MIN_ROWS.keys())
    ranges = [f"{t}!A1:A100000" for t in tabs]
    # When KDS is expected, also pull the full grids needed for the weekly /
    # period KDS-metric assertion (labor_daily for the existing join check,
    # labor_weekly + labor_period for the new per-item/late check).
    kds_grid_tabs = ["labor_daily", "labor_weekly", "labor_period"]
    if expect_kds:
        ranges.extend(f"{t}!A1:ZZ100000" for t in kds_grid_tabs)
    data = _sheets_batch_get(spreadsheet_id, token, ranges)
    value_ranges = data.get("valueRanges", [])

    counts: dict[str, int] = {}
    for t, vr in zip(tabs, value_ranges):
        vals = vr.get("values", [])
        counts[t] = max(len(vals) - 1, 0)

    model_kds_nonempty: bool | None = None
    weekly_values: list[list] | None = None
    period_values: list[list] | None = None
    if expect_kds and len(value_ranges) >= len(tabs) + len(kds_grid_tabs):
        grids = value_ranges[len(tabs):len(tabs) + len(kds_grid_tabs)]
        labor_daily_full = grids[0].get("values", [])
        weekly_values = grids[1].get("values", [])
        period_values = grids[2].get("values", [])
        model_kds_nonempty = _labor_daily_has_kds(labor_daily_full)

    raw_kds_count: int | None = None
    kds_min_date: str | None = None
    kds_max_date: str | None = None
    if expect_kds:
        try:
            raw = _sheets_batch_get(raw_square_sid, token, ["kds_daily!A1:A100000"])
            rkv = (raw.get("valueRanges") or [{}])[0].get("values", [])
            raw_kds_count = max(len(rkv) - 1, 0)
            # Column A of kds_daily is date_local (plain ISO). Derive the
            # KDS-covered date range so the weekly/period assertion only fires
            # for overlapping rows (KDS coverage starts 2026-04-24).
            kds_dates = [
                d for d in (_parse_sheet_iso_date(r[0]) for r in rkv[1:] if r)
                if d is not None
            ]
            if kds_dates:
                kds_min_date = min(kds_dates)
                kds_max_date = max(kds_dates)
        except Exception as exc:  # noqa: BLE001
            print(f"  [verify_model] could not read raw kds_daily (treating "
                  f"as unknown): {exc}")
            raw_kds_count = None

    return {
        "tab_row_counts": counts,
        "model_kds_columns_nonempty": model_kds_nonempty,
        "raw_kds_row_count": raw_kds_count,
        "weekly_values": weekly_values,
        "period_values": period_values,
        "kds_min_date": kds_min_date,
        "kds_max_date": kds_max_date,
    }


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
    from skills.adp_run_automation.runner import download_adp_bundle
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


@dataclass
class PipelineResult:
    """Outcome of a parallel data-gathering pipeline."""
    name: str
    success: bool = False
    error: Exception | None = None
    artifacts: dict[str, pathlib.Path | None] = field(default_factory=dict)
    master_stats: dict[str, int] = field(default_factory=dict)


def _run_square_pipeline(
    *,
    gap_start: datetime.date,
    end_date: datetime.date,
    store: str,
    headed: bool,
    refresh_date: datetime.date,
    dry_run: bool,
    skip_kds: bool = False,
) -> PipelineResult:
    """Thread 1: Square transactions + item sales + KDS scrape → consolidate CSV → GCS upload."""
    result = PipelineResult(name="square")
    try:
        if dry_run:
            print("[square_pipeline] DRY RUN — skipped.")
            result.success = True
            return result

        from skills.square_tips.runner import (
            _ensure_logged_in,
            _set_date_range,
            _trigger_export_and_download,
            TRANSACTIONS_URL,
            _acquire_scrape_lock,
            _release_scrape_lock,
            download_item_sales,
            download_kds_report,
        )
        from skills._browser_runtime.runtime import (
            DOWNLOADS_DIR as _DL_DIR,
            is_fresh_download,
            launch_persistent,
        )
        import re as _re

        # Check if transactions CSV already exists (idempotency).
        expected_txn = _DL_DIR / (
            f"transactions-{gap_start.isoformat()}-"
            f"{(end_date + datetime.timedelta(days=1)).isoformat()}.csv"
        )
        expected_items = _DL_DIR / (
            f"items-{gap_start.isoformat()}-"
            f"{(end_date + datetime.timedelta(days=1)).isoformat()}.csv"
        )
        txn_fresh = is_fresh_download(expected_txn)
        items_fresh = is_fresh_download(expected_items)

        # Check KDS freshness
        from skills._browser_runtime.runtime import DOWNLOADS_DIR as _DL_DIR2
        expected_kds = _DL_DIR2 / (
            f"kds-{gap_start.isoformat()}-"
            f"{(end_date + datetime.timedelta(days=1)).isoformat()}.csv"
        )
        kds_fresh = is_fresh_download(expected_kds) if not skip_kds else True
        needs_kds = not skip_kds and not kds_fresh and not step_already_done(refresh_date, "square_kds")

        if txn_fresh and items_fresh and (not needs_kds or kds_fresh):
            csv_path = expected_txn
            item_csv_path = expected_items
            kds_csv_path = expected_kds if not skip_kds and expected_kds.exists() else None
            print(f"[square_pipeline] SKIP browser — all CSVs fresh on disk")
        else:
            _acquire_scrape_lock(store)
            try:
                with launch_persistent(
                    portal="square", headed=headed, slow_mo_ms=50,
                ) as (ctx, page):
                    _ensure_logged_in(page, store=store)

                    # Download transactions
                    if txn_fresh:
                        csv_path = expected_txn
                        print(f"[square_pipeline] transactions already fresh: {csv_path}")
                    else:
                        page.goto(TRANSACTIONS_URL, wait_until="domcontentloaded")
                        page.locator("button").filter(
                            has_text=_re.compile(r"\d{2}/\d{2}/\d{4}")
                        ).first.wait_for(state="visible", timeout=30_000)
                        page.wait_for_timeout(1_500)
                        _set_date_range(page, start=gap_start, end=end_date)
                        csv_path = _trigger_export_and_download(
                            page, start=gap_start, end=end_date,
                        )
                        print(f"[square_pipeline] transactions OK → {csv_path}")

                    # Download item sales in the same session
                    if items_fresh:
                        item_csv_path = expected_items
                        print(f"[square_pipeline] item sales already fresh: {item_csv_path}")
                    else:
                        item_csv_path = download_item_sales(
                            page, start_date=gap_start, end_date=end_date, store=store,
                        )
                        print(f"[square_pipeline] item sales OK → {item_csv_path}")

                    # Download KDS report in the same session
                    if needs_kds:
                        kds_csv_path = download_kds_report(
                            page, start_date=gap_start, end_date=end_date, store=store,
                        )
                        print(f"[square_pipeline] KDS OK → {kds_csv_path}")
                    else:
                        kds_csv_path = expected_kds if expected_kds.exists() else None
            finally:
                _release_scrape_lock()

        result.artifacts["square_csv"] = csv_path
        result.artifacts["item_sales_csv"] = item_csv_path
        result.artifacts["kds_csv"] = kds_csv_path

        if csv_path is not None:
            total, added = _consolidate_into_master(gap_csv=csv_path)
            result.master_stats = {"master_rows": total, "rows_added": added}
            print(f"[square_pipeline] consolidate OK — master={total}, added={added}")

        try:
            upload_scrape_artifacts(
                refresh_date=refresh_date,
                download_dir=DOWNLOAD_DIR,
                square_csv=csv_path if isinstance(csv_path, pathlib.Path) else None,
                master_csv=MASTER_TXN_CSV if MASTER_TXN_CSV.exists() else None,
                item_sales_csv=item_csv_path if isinstance(item_csv_path, pathlib.Path) else None,
                kds_csv=kds_csv_path if isinstance(kds_csv_path, pathlib.Path) else None,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[square_pipeline] WARN: GCS upload failed (non-fatal): {exc}")

        result.success = True
    except Exception as exc:  # noqa: BLE001
        result.success = False
        result.error = exc
        print(f"[square_pipeline] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    return result


def _run_adp_pipeline(
    *,
    store: str,
    target_date: datetime.date | None,
    include_earnings: bool,
    headed: bool,
    refresh_date: datetime.date,
    dry_run: bool,
) -> PipelineResult:
    """Thread 2: ADP Timecard + Earnings scrape → GCS upload."""
    result = PipelineResult(name="adp")
    try:
        if dry_run:
            print("[adp_pipeline] DRY RUN — skipped.")
            result.success = True
            return result

        bundle = _adp_bundle_then_raise(
            store=store,
            target_date=target_date,
            include_earnings=include_earnings,
            headed=headed,
        )
        result.artifacts["adp_timecard_xlsx"] = bundle.get("timecard_xlsx")
        result.artifacts["adp_earnings_xlsx"] = bundle.get("earnings_xlsx")
        print(f"[adp_pipeline] bundle OK → {list(bundle.keys())}")

        adp_tc = result.artifacts.get("adp_timecard_xlsx")
        adp_er = result.artifacts.get("adp_earnings_xlsx")
        if not isinstance(adp_tc, pathlib.Path):
            tc_glob = sorted(DOWNLOAD_DIR.glob("Timecard-*.xlsx"))
            adp_tc = tc_glob[-1] if tc_glob else None
        if not isinstance(adp_er, pathlib.Path):
            er_glob = sorted(DOWNLOAD_DIR.glob("Earnings-*.xlsx"))
            adp_er = er_glob[-1] if er_glob else None
        if adp_tc or adp_er:
            try:
                upload_scrape_artifacts(
                    refresh_date=refresh_date,
                    download_dir=DOWNLOAD_DIR,
                    adp_timecard_xlsx=adp_tc,
                    adp_earnings_xlsx=adp_er,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[adp_pipeline] WARN: GCS upload failed (non-fatal): {exc}")

        result.success = True
    except Exception as exc:  # noqa: BLE001
        result.success = False
        result.error = exc
        print(f"[adp_pipeline] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    return result


def _run_review_fetch(
    *,
    store: str,
    dry_run: bool,
) -> PipelineResult:
    """Thread 3: Fetch ClickUp review messages and cache locally as JSON.

    Does NOT do attribution — that requires punch data from raw sheets
    and must run after write_raw_sheets + update_model_sheet.
    """
    from agents.bhaga.scripts.process_reviews import (  # noqa: PLC0415
        REVIEW_CHANNEL_ID, CLICKUP_TEAM_ID, REVIEW_CHANNEL_NAME,
        _load_profile as _load_review_profile,
        _read_config_tab, _latest_review_ts_ms,
        BONUS_START_DATE, CT as REVIEW_CT,
        fetch_review_messages,
    )
    from core.config_loader import (  # noqa: PLC0415
        refresh_access_token as _refresh_token,
        resolve_sheet_id as _resolve_sid,
    )

    result = PipelineResult(name="review_fetch")
    try:
        if dry_run:
            print("[review_fetch] DRY RUN — skipped.")
            result.success = True
            return result

        profile = _load_review_profile(store)
        raw_sheet_cfg = profile["google_sheets"].get("bhaga_review_raw")
        if not raw_sheet_cfg or not raw_sheet_cfg.get("spreadsheet_id"):
            print("[review_fetch] WARN: no bhaga_review_raw configured — skipping.")
            result.success = True
            return result

        raw_sid = _resolve_sid("bhaga_review_raw", profile)
        token = _refresh_token(store)

        latest_in_sheet_ms = _latest_review_ts_ms(raw_sid, token)
        if latest_in_sheet_ms is not None:
            since_ts_ms = latest_in_sheet_ms
        else:
            bonus_start_dt = datetime.datetime.combine(
                BONUS_START_DATE, datetime.time.min, tzinfo=REVIEW_CT,
            )
            since_ts_ms = int(bonus_start_dt.timestamp() * 1000) - 1

        msgs = fetch_review_messages(
            since_ts_ms=since_ts_ms, max_pages=40,
        )
        print(f"[review_fetch] fetched {len(msgs)} messages from ClickUp")

        cache_path = DOWNLOAD_DIR / "review-messages-prefetched.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(msgs, default=str), encoding="utf-8")
        result.artifacts["prefetched_messages"] = cache_path
        result.success = True
    except Exception as exc:  # noqa: BLE001
        result.success = False
        result.error = exc
        print(f"[review_fetch] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
    return result


# ── OTP availability gate helpers ──────────────────────────────────
#
# These decide, BEFORE launching any browser, which portals this run will
# actually need an OTP for. The rule is intentionally simple and matches the
# zero-OTP happy path: a portal needs an OTP iff its scrape will launch a
# browser this run (architecture is stateless — every browser launch does a
# fresh login → 2FA). If GCS cache + freshness markers already satisfy a
# step, NO browser launches and NO READY request is posted.


def _square_will_launch_browser(
    *,
    needs_square: bool,
    gap_start: datetime.date,
    end_date: datetime.date,
    refresh_date: datetime.date,
    skip_kds: bool,
) -> bool:
    """Mirror _run_square_pipeline's freshness gate to predict a browser launch."""
    if not needs_square:
        return False
    from skills._browser_runtime.runtime import DOWNLOADS_DIR as _DL, is_fresh_download

    plus1 = (end_date + datetime.timedelta(days=1)).isoformat()
    exp_txn = _DL / f"transactions-{gap_start.isoformat()}-{plus1}.csv"
    exp_items = _DL / f"items-{gap_start.isoformat()}-{plus1}.csv"
    exp_kds = _DL / f"kds-{gap_start.isoformat()}-{plus1}.csv"
    txn_fresh = is_fresh_download(exp_txn)
    items_fresh = is_fresh_download(exp_items)
    kds_fresh = is_fresh_download(exp_kds) if not skip_kds else True
    needs_kds = (
        (not skip_kds)
        and not kds_fresh
        and not step_already_done(refresh_date, "square_kds")
    )
    # Pipeline SKIPS the browser only when txn AND items are fresh AND KDS is
    # either not needed or already fresh.
    return not (txn_fresh and items_fresh and (not needs_kds or kds_fresh))


def _adp_will_launch_browser(
    *,
    needs_adp: bool,
    target_date: datetime.date | None,
    include_earnings: bool,
) -> bool:
    """Mirror download_adp_bundle's Layer-A gate to predict a browser launch."""
    if not needs_adp:
        return False
    from skills.adp_run_automation.runner import (
        DOWNLOADS_DIR as _DL,
        _xlsx_fresh_for_target,
    )

    today = datetime.date.today()
    tc = _DL / f"Timecard-{today.isoformat()}.xlsx"
    er = _DL / f"Earnings-and-Hours-V1-{today.isoformat()}.xlsx"
    tc_fresh = _xlsx_fresh_for_target(tc, target_date=target_date, min_bytes=10_000)
    er_fresh = (
        _xlsx_fresh_for_target(er, target_date=target_date, min_bytes=5_000)
        if include_earnings
        else False
    )
    needs_timecard = not tc_fresh
    needs_earnings = include_earnings and not er_fresh
    return needs_timecard or needs_earnings


def _execute_pipelines(
    specs: dict, *, serialize_otp: bool
) -> dict:
    """Run the data-gathering pipelines, returning {name: PipelineResult}.

    ``specs`` maps a pipeline name to a zero-arg callable returning a
    PipelineResult. Exceptions are captured into a failed PipelineResult so
    the caller's collection loop has a uniform contract.

    When ``serialize_otp`` is True and BOTH OTP-needing portals (square + adp)
    will run, they are driven BACK-TO-BACK rather than concurrently so the
    operator gets one fresh code at a time (codes can't be told apart if two
    SMS land together, and the pending-portal lookup is single-valued). Any
    non-OTP pipeline (review_fetch) still runs concurrently.
    """
    results: dict = {}

    def _capture(name: str, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            pr = PipelineResult(name=name)
            pr.success = False
            pr.error = exc
            return pr

    otp_names = [n for n in ("square", "adp") if n in specs]
    if serialize_otp and len(otp_names) > 1:
        other = {n: f for n, f in specs.items() if n not in otp_names}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(other) or 1)
        ) as pool:
            other_futs = {pool.submit(_capture, n, f): n for n, f in other.items()}
            # OTP portals strictly sequential (one fresh code at a time).
            for name in otp_names:
                results[name] = _capture(name, specs[name])
            for fut, n in other_futs.items():
                results[n] = fut.result()
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(_capture, n, f): n for n, f in specs.items()}
        for fut, n in futs.items():
            results[n] = fut.result()
    return results


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
    cli.add_argument("--skip-kds", action="store_true",
                     help="Skip KDS performance report scrape.")
    cli.add_argument("--skip-timecard", action="store_true")
    cli.add_argument("--skip-adp", action="store_true",
                     help="Alias for --skip-timecard (skip ADP scrape).")
    cli.add_argument("--skip-reviews", action="store_true",
                     help="Skip the Google review bonus refresh step.")
    cli.add_argument("--skip-model", action="store_true",
                     help="Skip the final Model-sheet refresh (raw downloads only).")
    # Per-source date range overrides
    cli.add_argument("--square-from", default=None, metavar="DATE",
                     help="Override Square scrape start date (YYYY-MM-DD). Default: gap_start.")
    cli.add_argument("--square-to", default=None, metavar="DATE",
                     help="Override Square scrape end date (YYYY-MM-DD). Default: refresh_date.")
    cli.add_argument("--adp-from", default=None, metavar="DATE",
                     help="Override ADP target start date (YYYY-MM-DD). Default: derived from gap.")
    cli.add_argument("--adp-to", default=None, metavar="DATE",
                     help="Override ADP target end date (YYYY-MM-DD). Default: refresh_date.")
    cli.add_argument("--adp-pay-period", default=None, metavar="PERIOD",
                     help="Override ADP pay period selection (e.g. 'current', 'last', 'all').")
    cli.add_argument("--reviews-since", default=None, metavar="DATE",
                     help="Override reviews anchor timestamp (YYYY-MM-DD). Default: auto from sheet.")
    cli.add_argument("--reviews-until", default=None, metavar="DATE",
                     help="Override reviews end cap (YYYY-MM-DD). Default: data_window_end.")
    cli.add_argument("--dry-run", action="store_true",
                     help="Print steps but do not actually scrape.")
    cli.add_argument("--no-slack", action="store_true",
                     help="Suppress all Slack messages (overrides notify.py).")
    args = cli.parse_args()

    # Unify --skip-adp / --skip-timecard
    if args.skip_adp:
        args.skip_timecard = True

    if args.no_slack:
        os.environ["BHAGA_SLACK_DISABLED"] = "1"

    # --date wins; otherwise honor the REFRESH_DATE env var (set by the cloud
    # webhook's job-execution trigger when resuming from a pending checkpoint),
    # falling back to today CT for the nightly cron.
    date_arg = args.date or os.environ.get("REFRESH_DATE") or None
    refresh_date = (
        datetime.date.fromisoformat(date_arg) if date_arg else _today_ct()
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

    # Fresh install: this is a full backfill from data_start, not an
    # incremental nightly.  Two consequences:
    #   1. ADP Timecard must select ALL pay periods (target_date=None triggers
    #      "Select All" in the pay-period dropdown) rather than just the single
    #      period containing refresh_date.  One login, one OTP, all data.
    #   2. Earnings should always be included so wage-rate data is present for
    #      the model sheet (override the Mon/Tue auto-gate).
    is_fresh_install = prev_end is None and "fresh install" in gap_source
    adp_target_date: datetime.date | None = None if is_fresh_install else refresh_date

    include_rates = (
        True if is_fresh_install  # always pull earnings on backfill
        else (False if args.skip_rates else _should_run_rates(override=args.include_rates if args.include_rates != "auto" else None))
    )

    headed = not args.headless  # default headed

    print(f"\n{'='*60}")
    print(f"BHAGA daily_refresh  store={args.store}  refresh_date={refresh_date.isoformat()}")
    print(f"  gap source:     {gap_source}")
    print(f"  gap window:     {gap_start.isoformat()} → {refresh_date.isoformat()}"
          + ("  (empty — nothing to scrape)" if not needs_square_scrape and not args.skip_square else ""))
    print(f"  fresh_install:  {is_fresh_install}")
    print(f"  adp_target:     {adp_target_date!r}{'  (Select All pay periods)' if adp_target_date is None else ''}")
    print(f"  include_rates:  {include_rates}")
    print(f"  headed:         {headed}")
    print(f"  dry_run:        {args.dry_run}")
    print(f"{'='*60}")

    # ── Resolve per-source date overrides ──────────────────────────────
    square_from = (
        datetime.date.fromisoformat(args.square_from) if args.square_from
        else gap_start
    )
    square_to = (
        datetime.date.fromisoformat(args.square_to) if args.square_to
        else refresh_date
    )

    if args.adp_pay_period == "all":
        adp_target_date = None  # triggers "Select All" in the pay-period dropdown
    elif args.adp_to:
        adp_target_date = datetime.date.fromisoformat(args.adp_to)
    # else: adp_target_date was already set above (None for fresh install, refresh_date otherwise)

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
    review_prefetch_path: pathlib.Path | None = None

    # ════════════════════════════════════════════════════════════════════
    # Phase 1: PARALLEL data gathering — Square, ADP, and review-fetch
    # run concurrently. Each gets its own error handling; a failure in
    # one does NOT block the others.
    # ════════════════════════════════════════════════════════════════════
    needs_square_txn = needs_square_scrape and not step_already_done(refresh_date, "square_transactions")
    needs_square_kds = needs_square_scrape and not args.skip_kds and not step_already_done(refresh_date, "square_kds")
    needs_square = needs_square_txn or needs_square_kds
    needs_adp = not args.skip_timecard and not step_already_done(refresh_date, "adp_reports")
    needs_review_fetch = not args.skip_reviews

    if not needs_square and not args.skip_square and not needs_square_scrape:
        print("[square_transactions] SKIPPED — already covered through refresh_date.")

    # ── GCS cache PRE-restore (no-OTP retry path) ──────────────────────
    # On a fresh Cloud Run container the scrape CSVs are not on disk, so the
    # Square pipeline's is_fresh_download() check would fail and fall through
    # to a browser login (→ Square 2FA OTP). For an already-scraped date the
    # CSVs live in GCS, so we restore them HERE — before the parallel phase —
    # which makes is_fresh_download() return True inside _run_square_pipeline
    # and SKIPS the browser entirely. This is what makes "retry an
    # already-scraped date with cleared markers" cost ZERO OTP.
    #
    # Safe for the normal nightly: a brand-new refresh_date has no cache yet,
    # so download_cached_files is a graceful no-op and the browser scrapes
    # as usual. The post-parallel restore below still runs to cover the
    # master CSV needed by write_raw_sheets when Square is skipped outright.
    if not args.dry_run and needs_square:
        print("\n[gcs_cache] pre-restoring scrape cache (if any) so a retry "
              "of an already-scraped date skips the browser / OTP...")
        try:
            pre_restored = download_cached_files(
                refresh_date=refresh_date,
                download_dir=DOWNLOAD_DIR,
            )
            if pre_restored:
                print(f"  [gcs_cache] pre-restored {len(pre_restored)} file(s) "
                      f"from GCS before scrape decision")
            else:
                print("  [gcs_cache] no cached files for this date — "
                      "Square will scrape fresh (expected on a new date)")
        except Exception as exc:  # noqa: BLE001
            print(f"  [gcs_cache] WARN: pre-restore failed (non-fatal): {exc}")

    # ── OTP availability gate (two-step READY handshake) ──────────────
    # Decide which portals THIS run will actually need an OTP for (i.e. will
    # launch a browser). If none, this is the zero-OTP happy path — no READY
    # request is ever posted. If at least one will, consult the pending
    # checkpoint:
    #   - no checkpoint        → post ONE READY request covering all portals,
    #                            persist the checkpoint, and EXIT CLEANLY (0).
    #   - checkpoint, no READY → already-outstanding request; exit cleanly
    #                            (or, if 48h elapsed, skip ONLY the OTP steps).
    #   - checkpoint + READY   → operator is active; proceed to trigger a
    #                            FRESH code per portal back-to-back.
    otp_portals: list[str] = []
    if not args.dry_run:
        if _square_will_launch_browser(
            needs_square=needs_square, gap_start=square_from,
            end_date=square_to, refresh_date=refresh_date, skip_kds=args.skip_kds,
        ):
            otp_portals.append("Square")
        if _adp_will_launch_browser(
            needs_adp=needs_adp, target_date=adp_target_date,
            include_earnings=include_rates,
        ):
            otp_portals.append("ADP")

    serialize_otp = False
    if otp_portals:
        decision, info = otp_gate.evaluate(refresh_date, otp_portals)
        print(f"[otp_gate] portals={otp_portals} decision={decision} "
              f"({info.get('reason')})")
        if decision == otp_gate.EXIT_PENDING:
            if info.get("first_request"):
                _adapter_save_pending_otp(
                    refresh_date, otp_portals,
                    requested_at=datetime.datetime.now(CT).isoformat(),
                    agent="bhaga",
                )
                ready_request(date=refresh_date.isoformat(), portals=otp_portals)
                print("[otp_gate] posted READY request + checkpoint; exiting "
                      "cleanly (exit 0). Will resume when operator replies READY.")
            else:
                print("[otp_gate] READY request already outstanding; exiting "
                      "cleanly without re-pinging the operator.")
            return 0
        if decision == otp_gate.SKIP_OTP:
            otp_skipped_alert(date=refresh_date.isoformat(), portals=otp_portals)
            _adapter_clear_pending_otp(refresh_date)
            if "Square" in otp_portals:
                args.skip_square = True
                needs_square = False
            if "ADP" in otp_portals:
                args.skip_timecard = True
                needs_adp = False
            print(f"[otp_gate] 48h cap hit — skipped {otp_portals}; finishing "
                  "every step that does NOT need an OTP.")
        elif decision == otp_gate.PROCEED:
            # READY in hand → operator is active now. Use a SHORT bounded wait
            # per code (the long wait already happened, for free, before READY).
            # Serialize OTP portals so two SMS can't collide.
            os.environ.setdefault("BHAGA_OTP_WAIT_S", "900")
            serialize_otp = len(otp_portals) > 1

    # ── Build + run the data-gathering pipelines ──────────────────────
    pipeline_specs: dict = {}
    if needs_square:
        pipeline_specs["square"] = functools.partial(
            _run_square_pipeline,
            gap_start=square_from,
            end_date=square_to,
            store=args.store,
            headed=headed,
            refresh_date=refresh_date,
            dry_run=args.dry_run,
            skip_kds=args.skip_kds,
        )
    if needs_adp:
        if is_fresh_install:
            print(f"  [adp] FRESH INSTALL: target_date=None (Select All pay periods), include_earnings=True")
        pipeline_specs["adp"] = functools.partial(
            _run_adp_pipeline,
            store=args.store,
            target_date=adp_target_date,
            include_earnings=include_rates,
            headed=headed,
            refresh_date=refresh_date,
            dry_run=args.dry_run,
        )
    if needs_review_fetch:
        pipeline_specs["review_fetch"] = functools.partial(
            _run_review_fetch,
            store=args.store,
            dry_run=args.dry_run,
        )

    results = _execute_pipelines(pipeline_specs, serialize_otp=serialize_otp)

    # Collect results from all pipelines (executor captured exceptions into
    # failed PipelineResults, so the contract is uniform here).
    otp_portal_failed = False
    for pipeline_name, pr in results.items():
        if not pr.success:
            if pr.error:
                failures.append((pipeline_name, pr.error))
                if pipeline_name in ("square", "adp"):
                    otp_portal_failed = True
                try:
                    failure_alert(
                        step=pipeline_name, exception=pr.error,
                        date=refresh_date.isoformat(),
                    )
                except Exception:  # noqa: BLE001, S110
                    pass
            continue

        if pipeline_name == "square":
            artifacts["square_csv"] = pr.artifacts.get("square_csv")
            artifacts["item_sales_csv"] = pr.artifacts.get("item_sales_csv")
            artifacts["kds_csv"] = pr.artifacts.get("kds_csv")
            master_stats = pr.master_stats
            try:
                mark_step_done(refresh_date, "square_transactions",
                               note=f"rows_added={pr.master_stats.get('rows_added', 0)}")
                mark_step_done(refresh_date, "consolidate_csv")
                if pr.artifacts.get("kds_csv"):
                    mark_step_done(refresh_date, "square_kds")
            except Exception as mark_exc:  # noqa: BLE001
                print(f"  [square] WARN: marker write failed: {mark_exc}")

        elif pipeline_name == "adp":
            artifacts["adp_timecard_xlsx"] = pr.artifacts.get("adp_timecard_xlsx")
            artifacts["adp_earnings_xlsx"] = pr.artifacts.get("adp_earnings_xlsx")
            try:
                mark_step_done(refresh_date, "adp_reports")
            except Exception as mark_exc:  # noqa: BLE001
                print(f"  [adp] WARN: marker write failed: {mark_exc}")

        elif pipeline_name == "review_fetch":
            review_prefetch_path = pr.artifacts.get("prefetched_messages")

    # OTP portals completed (or none were needed / they were skipped at the
    # cap): tear down the pending checkpoint so a same-day rerun doesn't think
    # the run is still awaiting READY. If an OTP portal FAILED after READY we
    # keep the checkpoint (ready_received stays True) so the retry proceeds
    # straight to a fresh code without re-asking for availability.
    if otp_portals and not otp_portal_failed and not args.dry_run:
        try:
            _adapter_clear_pending_otp(refresh_date)
        except Exception as exc:  # noqa: BLE001
            print(f"[otp_gate] WARN: could not clear pending checkpoint: {exc}")

    # ── GCS cache: restore missing files before downstream steps ──
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

    # ════════════════════════════════════════════════════════════════════
    # Phase 2: SEQUENTIAL downstream — write raw sheets, update model,
    # then attribution-phase of process_reviews.
    # ════════════════════════════════════════════════════════════════════

    failed_steps = {name for name, _ in failures}
    square_ok = (
        "square" not in failed_steps
        and "square_transactions" not in failed_steps
        and "consolidate_csv" not in failed_steps
        and not args.skip_square
    )
    raw_sheets_ok = False
    if square_ok or not args.skip_timecard:
        gap_csv_for_check = artifacts.get("square_csv") if isinstance(artifacts.get("square_csv"), pathlib.Path) else None

        def _write_raw_sheets_step():
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

    failed_steps = {name for name, _ in failures}
    if not args.skip_model and (raw_sheets_ok or (args.skip_square and args.skip_timecard)):
        model_cmd = [
            sys.executable, "-m", "agents.bhaga.scripts.update_model_sheet",
            "--store", args.store,
        ]
        if os.environ.get("BHAGA_DATASTORE", "").lower() == "bigquery":
            model_cmd += ["--data-source", "bigquery"]
        ok, val = run_step(
            "update_model_sheet",
            lambda: subprocess.run(
                model_cmd,
                cwd=str(PROJECT_ROOT), check=True,
            ),
            refresh_date=refresh_date,
            dry_run=args.dry_run,
        )
        if not ok:
            failures.append(("update_model_sheet", val))

    # Step: Google Review attribution (sequential, uses pre-fetched messages).
    # Architecture rule: ALL data fetching happens in the parallel phase.
    # process_reviews REQUIRES the pre-fetched JSON from review_fetch.
    # If review_fetch failed or produced no file, process_reviews is skipped.
    review_fetch_ok = "review_fetch" not in failed_steps
    if not args.skip_reviews and raw_sheets_ok and review_fetch_ok:
        if not review_prefetch_path or not review_prefetch_path.exists():
            print("[process_reviews] SKIPPED — review_fetch produced no output file.")
        else:
            review_cmd = [
                sys.executable, "-m", "agents.bhaga.scripts.process_reviews",
                "--store", args.store,
            ]
            if args.no_slack:
                review_cmd.append("--no-slack")
            review_cmd.extend(["--prefetched-messages", str(review_prefetch_path)])
            if args.reviews_since:
                review_cmd.extend(["--since", args.reviews_since])

            ok, val = run_step(
                "process_reviews",
                lambda: subprocess.run(
                    review_cmd, cwd=str(PROJECT_ROOT), check=True,
                ),
                refresh_date=refresh_date,
                dry_run=args.dry_run,
            )
            if not ok:
                failures.append(("process_reviews", val))
    elif args.skip_reviews:
        print("[process_reviews] SKIPPED — --skip-reviews flag set.")
    elif not review_fetch_ok:
        print("[process_reviews] SKIPPED — review_fetch failed in parallel phase.")
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

    # ── Model-sheet verification (built into the pipeline) ─────────
    # Runs on EVERY execution that actually rebuilt the model. Reads the
    # Model workbook back and asserts the expected tabs are non-empty (the
    # period tabs in particular — empty period tabs were the 2026-05-27
    # bug). On failure: loud RuntimeError + failure_alert DM + non-zero
    # exit, so the operator is notified the same night. Skipped when the
    # model wasn't refreshed this run (--skip-model / dry-run / update
    # failed) so we never false-positive on legitimately-empty cases.
    if update_model_ran:
        expect_kds = not args.skip_kds
        try:
            raw_square_sid = resolve_sheet_id("bhaga_square_raw", profile)
            vdata = _read_model_verification_data(
                spreadsheet_id=spreadsheet_id,
                store=args.store,
                raw_square_sid=raw_square_sid,
                expect_kds=expect_kds,
            )
            counts = vdata["tab_row_counts"]
            print(f"\n[verify_model] model tab row counts: {counts}")
            if expect_kds:
                print(f"[verify_model] raw kds_daily rows={vdata['raw_kds_row_count']}; "
                      f"model labor_daily KDS columns non-empty="
                      f"{vdata['model_kds_columns_nonempty']}; "
                      f"KDS coverage={vdata['kds_min_date']}..{vdata['kds_max_date']}")
            assert_model_tabs_populated(
                tab_row_counts=counts,
                expect_kds=expect_kds,
                raw_kds_row_count=vdata["raw_kds_row_count"],
                model_kds_columns_nonempty=vdata["model_kds_columns_nonempty"],
                weekly_values=vdata["weekly_values"],
                period_values=vdata["period_values"],
                kds_min_date=vdata["kds_min_date"],
                kds_max_date=vdata["kds_max_date"],
            )
            print("[verify_model] OK — all expected model tabs are populated "
                  "(incl. weekly/period KDS metrics).")
        except RuntimeError as exc:
            print(f"\n!!! MODEL VERIFICATION FAILED: {exc}", file=sys.stderr)
            try:
                failure_alert(
                    step="verify_model_sheet",
                    exception=exc,
                    date=refresh_date.isoformat(),
                    extra=(
                        "update_model_sheet ran but the rebuilt Model sheet is "
                        "missing expected non-empty tabs (e.g. labor_period / "
                        "period_summary at 0 rows, or KDS columns empty). The "
                        "model is NOT correct — investigate update_model_sheet "
                        "(period derivation, raw-sheet reads) before relying on "
                        "tonight's numbers. The other steps' .done markers are "
                        "written, so re-running will re-verify after a fix."
                    ),
                )
            except Exception:  # noqa: BLE001, S110
                pass
            return 1
        except Exception as exc:  # noqa: BLE001
            # A transport/read error while verifying shouldn't mask the run
            # as failed (the post-condition guard already covers the
            # data-advancement contract), but it must be loud.
            print(f"[verify_model] WARN: could not read back the model sheet "
                  f"for verification (non-fatal): {type(exc).__name__}: {exc}",
                  file=sys.stderr)

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
