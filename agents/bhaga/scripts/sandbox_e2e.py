#!/usr/bin/env python3
"""Zero-OTP, prod-like e2e for the BHAGA sales / labor / tip / model core.

Leases a slot from the pre-created sandbox pool (see ``sandbox_provision.py``),
clears + re-seeds it, replays scrape artifacts from the GCS cache (read-only)
into the sandbox RAW sheets, builds the MODEL into the sandbox, asserts the model
tabs are populated, prints evidence, then releases the slot. Runs against
isolated sandbox sheets — never the production workbooks.

LOCAL SMOKE (run before pushing — closes the "only caught in CI" gap):
    python3 -m agents.bhaga.scripts.sandbox_e2e --pr-number 0 --auto-window
Locally it authenticates as the operator (user creds) and uses a deterministic
slot; in CI it authenticates as the service account and Firestore-leases a slot.
Both exercise the same real Sheets I/O, so identity/permission/enablement issues
surface locally instead of on a PR.

STRUCTURAL no-OTP guarantee
---------------------------
This runner composes ONLY downstream replay code:

    gcs_cache.download_cached_files      (read-only GCS restore)
    backfill_from_downloads.main         (pure parsers -> sandbox RAW sheets)
    update_model_sheet.main              (sandbox RAW -> sandbox MODEL)

It never imports or invokes any Square / ADP / ClickUp scrape or login module,
so it can never launch a browser or trigger an OTP. ``test_sandbox_e2e_no_otp``
enforces this by importing this module in an isolated interpreter and asserting
no scrape/login module is present in ``sys.modules``.

Reviews are intentionally out of scope (they need a live ClickUp call); the e2e
proves the sales/labor/tip/model core. Item-level operations are picked up
automatically if/when ``backfill_item_lines_from_cache`` lands on main.

Usage:
    python3 -m agents.bhaga.scripts.sandbox_e2e --store palmetto \
        --pr-number 42 --start 2026-05-01 --end 2026-05-03
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import backfill_from_downloads, gcs_cache, sandbox_provision, update_model_sheet  # noqa: E402
from agents.bhaga.scripts.daily_refresh import CT, MODEL_VERIFY_MIN_ROWS, assert_model_tabs_populated  # noqa: E402
from agents.bhaga.scripts.model_semantics import (  # noqa: E402
    _header_resolver,
    _to_cents,
    assert_adp_reconciliation_present,
    assert_tip_pool_conserved,
)
from core.config_loader import (  # noqa: E402
    _load_production_sheet_ids,
    allow_production_read,
    refresh_access_token,
    resolve_sheet_id,
)
from skills.tip_ledger_writer import reader as raw_reader  # noqa: E402
from skills.tip_ledger_writer import writer as raw_writer  # noqa: E402

DOWNLOADS = backfill_from_downloads.DOWNLOADS

# PROD raw -> SANDBOX raw seeding sources for the no-OTP prod-data path.
# Each tuple: (reader attr on reader.py, writer attr on writer.py, store-profile
# google_sheets key, the row's ISO-date column for window filtering — None means
# the tab has no date column and is copied whole, e.g. per-employee wage rates).
_PROD_RAW_SOURCES: tuple[tuple[str, str, str, str | None], ...] = (
    ("read_raw_adp_shifts", "write_raw_adp_shifts", "bhaga_adp_raw", "date"),
    ("read_raw_adp_punches", "write_raw_adp_punches", "bhaga_adp_raw", "date"),
    ("read_raw_adp_rates", "write_raw_adp_rates", "bhaga_adp_raw", None),
    ("read_raw_square_transactions", "write_raw_square_transactions", "bhaga_square_raw", "date_local"),
    ("read_raw_square_daily_rollup", "write_raw_square_daily_rollup", "bhaga_square_raw", "date_local"),
    ("read_raw_square_item_lines", "write_raw_square_item_lines", "bhaga_square_raw", "date_local"),
    ("read_raw_square_item_daily_rollup", "write_raw_square_item_daily_rollup", "bhaga_square_raw", "date_local"),
    ("read_raw_kds_daily", "write_raw_kds_daily", "bhaga_square_raw", "date_local"),
)

# Shorter than MODEL_VERIFY_MIN_ROWS: the sandbox replay window is only a few
# cached days (cost), so biweekly period tabs may legitimately be empty. Nightly
# prod uses the full multi-period window and keeps the stricter contract there.
SANDBOX_E2E_VERIFY_MIN_ROWS: dict[str, int] = {
    "daily": 1,
    "labor_daily": 1,
    "labor_weekly": 1,
    "labor_daily_forecast": 1,
}

# The prod-raw path covers a FULL closed pay period, so the period-grain tabs
# MUST populate (a closed period is always complete) and the tip allocation is
# verifiable end-to-end. Stricter than the GCS-replay auto-window contract.
PROD_RAW_VERIFY_MIN_ROWS: dict[str, int] = {
    "daily": 1,
    "labor_daily": 1,
    "labor_weekly": 1,
    "labor_daily_forecast": 1,
    "labor_period": 1,
    "period_summary": 1,
    "tip_alloc_daily": 1,
    "tip_alloc_period": 1,
}

# Modules that, if ever imported by this runner's graph, would mean a scrape /
# login path is reachable. The no-OTP test asserts none of these load.
FORBIDDEN_MODULES: tuple[str, ...] = (
    "skills.square_tips.runner",
    "skills.adp_run_automation.runner",
    "skills.clickup_chat",
    "skills.slack.listener",
    "patchright",
    "playwright",
)


# ── Pure helpers (no I/O — unit-tested) ───────────────────────────


def dates_in_window(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """Inclusive list of dates from start..end. Raises if end < start."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]


def select_window(cached_dates: list[datetime.date], max_days: int) -> tuple[datetime.date, datetime.date]:
    """Pick the most recent ``max_days`` *cached* dates and return (start, end).

    NOTE: the returned (start, end) spans the ``max_days`` most-recent dates that
    actually have artifacts in GCS — NOT ``max_days`` calendar days. With a sparse
    cache the calendar span (end - start) can exceed ``max_days``; that's fine,
    because the replay only touches dates that are cache-backed. Keeps the e2e
    window small (cost) and always cache-backed (determinism), independent of any
    hardcoded calendar range. Raises if no dates are cached.
    """
    if not cached_dates:
        raise ValueError("no cached dates in GCS — cannot auto-select an e2e window")
    if max_days < 1:
        raise ValueError(f"max_days must be >= 1, got {max_days}")
    recent = sorted(cached_dates)[-max_days:]
    return recent[0], recent[-1]


def staging_environ(ids: dict[str, str]) -> dict[str, str]:
    """Env overlay that routes the pipeline to the sandbox sheets.

    BHAGA_SHEET_MODE=staging activates the resolve_sheet_id staging branch and
    the _assert_not_production_sheet guard; the per-key SIDs point it at the
    sandbox.
    """
    env = dict(sandbox_provision.staging_env(ids))
    env["BHAGA_SHEET_MODE"] = "staging"
    return env


def tab_counts_from_columns(raw: dict[str, list[list]]) -> dict[str, int]:
    """{tab: value_rows_incl_header} -> {tab: data_row_count} (header excluded)."""
    return {tab: max(0, len(rows) - 1) for tab, rows in raw.items()}


def filter_rows_to_window(
    rows: list[dict], date_field: str | None, start: datetime.date, end: datetime.date,
) -> list[dict]:
    """Keep rows whose ``date_field`` ISO value falls in [start, end] inclusive.

    ``date_field=None`` means the tab has no per-day date (e.g. wage_rates) and
    every row is kept. ISO ``YYYY-MM-DD`` strings sort lexicographically, so a
    string compare is correct and avoids parsing every cell.
    """
    if date_field is None:
        return list(rows)
    lo, hi = start.isoformat(), end.isoformat()
    out: list[dict] = []
    for r in rows:
        v = str(r.get(date_field, "")).strip()[:10]
        if lo <= v <= hi:
            out.append(r)
    return out


def format_evidence(report: dict) -> str:
    """Render a human/PR-comment-friendly evidence block from a run report."""
    lines: list[str] = []
    lines.append(f"### BHAGA sandbox e2e — PR #{report.get('pr_number')}")
    lines.append(f"- status: **{report.get('status', 'unknown')}**")
    if report.get("source"):
        lines.append(f"- source: **{report['source']}**")
    win = report.get("window", {})
    lines.append(f"- window: {win.get('start')} -> {win.get('end')} ({report.get('days')} day(s))")
    restored = report.get("restored_files", {})
    if restored:
        lines.append(f"- GCS files restored: {sum(restored.values())} "
                     f"({', '.join(f'{d}:{n}' for d, n in sorted(restored.items()))})")
    seeded = report.get("seeded_rows")
    if seeded:
        lines.append(f"- prod raw seeded into sandbox: {sum(seeded.values())} row(s) "
                     f"({', '.join(f'{t}:{n}' for t, n in sorted(seeded.items()))})")
    ts = report.get("seeded_training_shifts")
    if ts is not None:
        lines.append(f"- prod training_shifts overlay mirrored into sandbox: {ts} row(s)")
    cons = report.get("tip_pool_conservation")
    if cons:
        lines.append(f"- tip-pool conserved: {cons['dates_checked']} day(s), "
                     f"max residual {cons['max_residual_cents']}c")
    ex = report.get("exemptions")
    if ex:
        lines.append(
            f"- tip exemptions verified: {ex['worked_exempt_shifts_dropped']}/"
            f"{ex['exempt_shifts_checked']} worked exempt shift(s) dropped from tips; "
            f"whole-period-exempt {ex['whole_period_exempt'] or '[]'}; "
            f"partial-exempt {ex['partial_exempt'] or '[]'}; "
            f"redistributed on {', '.join(ex['exempt_days_redistributed']) or '[]'}; "
            f"period our_calc {ex['period_our_calc_cents']}c == pool {ex['period_pool_cents']}c"
        )
    counts = report.get("model_tab_counts") or {}
    if counts:
        lines.append("- model tab row counts:")
        for tab in sorted(counts):
            lines.append(f"    - `{tab}`: {counts[tab]}")
    if report.get("item_lines_ran"):
        lines.append("- item-level operations backfill: ran (module present)")
    if report.get("error"):
        lines.append(f"- error: `{report['error']}`")
    if report.get("slot") is not None:
        lines.append(f"- pool slot: {report['slot']}")
    teardown = report.get("teardown")
    if teardown is not None:
        slot = teardown.get("slot")
        released = teardown.get("released")
        lines.append(f"- teardown: slot {slot} {'released + cleared' if released else 'nothing to release'}")
    return "\n".join(lines)


def _item_lines_module_available() -> bool:
    """True iff the (forward-compatible) item-lines backfill script exists."""
    return importlib.util.find_spec(
        "agents.bhaga.scripts.backfill_item_lines_from_cache"
    ) is not None


# ── Thin composition wrappers (I/O) ───────────────────────────────


def _apply_staging_env(ids: dict[str, str]) -> None:
    os.environ.update(staging_environ(ids))


def _invoke_main(main_fn, argv: list[str]) -> int:
    """Call a script's argparse main() with a controlled argv."""
    old = sys.argv
    sys.argv = ["sandbox_e2e"] + argv
    try:
        return main_fn() or 0
    finally:
        sys.argv = old


def _replay_from_gcs(start: datetime.date, end: datetime.date) -> dict[str, int]:
    """Restore cached scrape artifacts for the window into the downloads dir."""
    restored: dict[str, int] = {}
    for d in dates_in_window(start, end):
        files = gcs_cache.download_cached_files(refresh_date=d, download_dir=DOWNLOADS)
        restored[d.isoformat()] = len(files)
    return restored


def _run_backfill(store: str, start: datetime.date, end: datetime.date) -> int:
    return _invoke_main(
        backfill_from_downloads.main,
        ["--store", store, "--start", start.isoformat(), "--end", end.isoformat()],
    )


def seed_sandbox_raw_from_prod(
    *, profile: dict, account: str, start: datetime.date, end: datetime.date,
) -> dict[str, int]:
    """Copy PROD raw Square+ADP rows for [start, end] into the SANDBOX raw sheets.

    The no-OTP alternative to the GCS replay: instead of re-parsing cached
    downloads, it reads the already-scraped PROD raw sheets directly and writes
    the windowed rows to the staging-resolved sandbox sheets, so the model build
    runs over real prod data with zero scrape/login.

    Isolation contract (hard-asserted per source): READS use the prod sid inside
    an explicit `allow_production_read()` scope (reading prod is sanctioned) and
    WRITES use the staging-resolved sandbox sid. The staging guard blocks every
    prod *write* even inside that read scope, so a misrouted write fails closed.
    Returns {tab: rows_written}.
    """
    prod_ids = _load_production_sheet_ids()
    seeded: dict[str, int] = {}
    for reader_attr, writer_attr, profile_key, date_field in _PROD_RAW_SOURCES:
        prod_sid = profile["google_sheets"][profile_key]["spreadsheet_id"]
        sandbox_sid = resolve_sheet_id(profile_key, profile)
        if prod_sid not in prod_ids:
            raise RuntimeError(
                f"seed: read sid {prod_sid!r} for {profile_key} is not a known "
                f"production sheet — refusing to seed from a non-prod source"
            )
        if sandbox_sid in prod_ids:
            raise RuntimeError(
                f"seed: refusing to WRITE production sheet {sandbox_sid!r} for "
                f"{profile_key} — sandbox isolation violated"
            )
        # Reading prod is sanctioned; scope it explicitly so the staging guard
        # permits this read while still blocking every prod *write*.
        with allow_production_read():
            rows = getattr(raw_reader, reader_attr)(prod_sid, account=account)
        windowed = filter_rows_to_window(rows, date_field, start, end)
        if windowed:
            # write_raw_* upserts (preserves non-matching keys), but determinism
            # is safe: provision() -> clear_slot() _batch_clears every tab in the
            # raw sheets before this seed, so only this window's rows are present
            # and the conservation check covers exactly the seeded period.
            getattr(raw_writer, writer_attr)(sandbox_sid, windowed, account=account)
        seeded[reader_attr.replace("read_raw_", "")] = len(windowed)
    return seeded


def seed_sandbox_training_shifts_from_prod(
    *, profile: dict, account: str, start: datetime.date, end: datetime.date,
) -> list[dict]:
    """Mirror the human-owned prod MODEL `training_shifts` overlay into the sandbox.

    The raw seed copies Square+ADP raw, but the per-shift tip-exemption overlay
    lives on the MODEL workbook's human-owned `training_shifts` tab — not a
    schema/pipeline tab, so ``seed_sandbox_raw_from_prod`` never touches it. The
    sandbox model build only honors the same exemptions as prod if this overlay
    is mirrored too; without it the sandbox would prove conservation but NOT the
    exemption (the gap this closes).

    Read-prod / write-sandbox, identical isolation contract to the raw seed: the
    prod READ is scoped by ``allow_production_read()`` (sanctioned) while the
    WRITE targets the staging-resolved sandbox model sid (a prod write fails
    closed via the staging guard). Only rows whose ``date`` falls in
    [start, end] are mirrored so the sandbox overlay matches exactly the period
    under test. Returns the list of mirrored ``{employee_name, date, note}``
    records (so the caller can derive the exempt set for verification).
    """
    prod_ids = _load_production_sheet_ids()
    prod_sid = profile["google_sheets"]["bhaga_model"]["spreadsheet_id"]
    sandbox_sid = resolve_sheet_id("bhaga_model", profile)
    if prod_sid not in prod_ids:
        raise RuntimeError(
            f"seed training_shifts: read sid {prod_sid!r} is not a known "
            f"production sheet — refusing to seed from a non-prod source"
        )
    if sandbox_sid in prod_ids:
        raise RuntimeError(
            f"seed training_shifts: refusing to WRITE production sheet "
            f"{sandbox_sid!r} — sandbox isolation violated"
        )
    token = refresh_access_token(account=account)
    # The overlay is operator-curated (fixed columns: employee_name|date|note),
    # so read it as a raw grid rather than through a schema reader.
    with allow_production_read():
        grid = raw_writer._read_tab(prod_sid, "training_shifts", token)
    lo, hi = start.isoformat(), end.isoformat()
    records: list[dict] = []
    for r in (grid[1:] if grid else []):
        if not r or not str(r[0]).strip():
            continue
        name = str(r[0]).strip()
        date = (str(r[1]).strip()[:10] if len(r) > 1 else "")
        note = (str(r[2]).strip() if len(r) > 2 else "")
        if not date or not (lo <= date <= hi):
            continue
        records.append({"employee_name": name, "date": date, "note": note})
    if records:
        raw_writer.write_training_shifts(sandbox_sid, records, account=account)
    return records


def _maybe_run_item_lines(store: str) -> bool:
    """Run the item-lines backfill if present (forward compat). Returns ran?"""
    if not _item_lines_module_available():
        return False
    import agents.bhaga.scripts.backfill_item_lines_from_cache as item_lines  # type: ignore
    # GCS is the default source; --local-only is dev/tests only. (The earlier
    # forward-compat call passed --gcs-only, which the landed script dropped as
    # redundant.)
    _invoke_main(item_lines.main, ["--store", store])
    return True


def _run_model_build(store: str) -> int:
    return _invoke_main(update_model_sheet.main, ["--store", store])


def _read_model_tab_counts(token: str, model_sid: str, tabs: list[str] | None = None) -> dict[str, int]:
    # One batchGet for all verify tabs instead of N single reads (quota-friendly).
    tabs = list(tabs) if tabs is not None else list(MODEL_VERIFY_MIN_ROWS)
    ranges = [f"{tab}!A1:A100000" for tab in tabs]
    by_range = sandbox_provision._batch_read_values(token, model_sid, ranges)
    raw = {tab: by_range.get(rng, []) for tab, rng in zip(tabs, ranges)}
    return tab_counts_from_columns(raw)


def _read_model_grids(token: str, model_sid: str, tabs: list[str]) -> dict[str, list[list]]:
    """Read several full tab grids in ONE batchGet (quota-friendly).

    Value-level assertions (conservation + exemptions) need the whole
    tip_alloc_daily and tip_alloc_period grids; fetching them in a single
    spreadsheets.values:batchGet round-trip avoids N separate reads against the
    60-req/min Sheets quota (the 429 we otherwise lean on backoff to absorb).
    """
    ranges = [f"{tab}!A1:ZZ100000" for tab in tabs]
    by_range = sandbox_provision._batch_read_values(token, model_sid, ranges)
    return {tab: by_range.get(rng, []) for tab, rng in zip(tabs, ranges)}


def _read_worked_hours(
    adp_sid: str, *, account: str, start: datetime.date, end: datetime.date,
) -> dict[tuple[str, str], float]:
    """{(canonical_employee, date): total_hours} for worked shifts in [start,end].

    Read from the SANDBOX ADP raw (already seeded) — the ground truth of who
    actually worked each day, so the exemption check can prove an exempt shift
    was worked yet dropped from tips. Only shifts with total_hours>0 are kept.
    """
    lo, hi = start.isoformat(), end.isoformat()
    out: dict[tuple[str, str], float] = {}
    for s in raw_reader.read_raw_adp_shifts(adp_sid, account=account):
        date = str(s.get("date", "")).strip()[:10]
        emp = str(s.get("employee_name", "")).strip()
        hours = float(s.get("total_hours") or 0)
        if emp and lo <= date <= hi and hours > 0:
            out[(emp, date)] = out.get((emp, date), 0.0) + hours
    return out


# Tip-pool conservation + adp reconciliation are the shared semantic checks
# (model_semantics): the SAME pure functions the nightly daily_refresh guard
# uses, so a regression can't pass one gate and fail the other. _to_cents /
# _header_resolver are re-exported here for the sandbox-only exemption check.


def assert_exemptions_applied(
    tip_alloc_daily_values: list[list],
    tip_alloc_period_values: list[list],
    exempt_shifts: set[tuple[str, str]],
    worked_hours: dict[tuple[str, str], float],
) -> dict:
    """Prove the per-shift training overlay dropped tips + redistributed + conserved.

    Data-driven (no hardcoded employee names). ``exempt_shifts`` is the set of
    ``(employee, date)`` pairs mirrored from the prod ``training_shifts`` overlay
    for the period; ``worked_hours`` maps every ``(employee, date)`` that has a
    real worked shift (from the sandbox ADP raw, total_hours>0) to its hours.

    The model OMITS an exempt shift from ``tip_alloc_daily`` entirely (its hours
    leave that day's denominator and the pool redistributes to the rest), so the
    proof is "worked the shift, yet received no tip share". Asserts:

      1. each exempt (employee, date) that was actually WORKED receives no tip
         share (absent from tip_alloc_daily, or present with our_share == 0), and
         at least one such worked-and-exempt shift exists (the overlay bites);
      2. on each exempt DAY the day's pool is still fully distributed to the
         remaining staff cent-exact (redistribution, no leak);
      3. WHOLE-period-exempt employees (every worked day exempt) get $0 over the
         period (absent from tip_alloc_period, or our_calc == 0); PARTIALLY-exempt
         employees keep a positive ``our_calc`` AND their period ``hours_worked``
         equals the sum of their NON-exempt worked hours (exempt hours dropped);
      4. period-level conservation: sum of period ``our_calc`` == sum of the
         distinct per-date ``day_pool`` (cent-exact).

    Returns a summary dict; raises RuntimeError on any violation.
    """
    if not tip_alloc_daily_values or len(tip_alloc_daily_values) < 2:
        raise RuntimeError("exemption check: tip_alloc_daily is empty")
    if not tip_alloc_period_values or len(tip_alloc_period_values) < 2:
        raise RuntimeError("exemption check: tip_alloc_period is empty")

    dcol = _header_resolver(tip_alloc_daily_values[0], "exemption check (daily)")
    di_date, di_emp = dcol("date", "date_local"), dcol("employee", "employee_name")
    di_pool, di_share = dcol("day_pool", "tip_pool_dollars"), dcol("our_share", "tip_allocation_dollars")
    dwidth = max(di_date, di_emp, di_pool, di_share)

    share_by: dict[tuple[str, str], int] = {}
    pool_by_date: dict[str, int] = {}
    share_sum_by_date: dict[str, int] = {}
    for row in tip_alloc_daily_values[1:]:
        if not row or len(row) <= dwidth or not str(row[di_date]).strip():
            continue
        date = str(row[di_date]).strip()[:10]
        emp = str(row[di_emp]).strip()
        share = _to_cents(row[di_share])
        share_by[(emp, date)] = share
        pool_by_date.setdefault(date, _to_cents(row[di_pool]))
        share_sum_by_date[date] = share_sum_by_date.get(date, 0) + share

    # (1) exempt shift took no tip share; prove on at least one WORKED shift.
    verified_worked_exempt = 0
    for (emp, date) in sorted(exempt_shifts):
        share = share_by.get((emp, date))
        if share is not None and share != 0:
            raise RuntimeError(
                f"exemption check: {emp} on {date} is training-exempt but "
                f"received our_share={share}c (expected dropped / 0)"
            )
        if worked_hours.get((emp, date), 0) > 0 and not share:
            verified_worked_exempt += 1
    if exempt_shifts and verified_worked_exempt == 0:
        raise RuntimeError(
            "exemption check: no exempt (employee,date) pair matched a real worked "
            "shift that was dropped from tips — the overlay had no provable effect "
            "(sandbox mirror or ADP seed broken?)"
        )

    # (2) redistribution: each exempt day's pool fully distributed, no leak.
    exempt_dates = {d for (_e, d) in exempt_shifts if d in pool_by_date}
    for date in sorted(exempt_dates):
        pool, allocated = pool_by_date[date], share_sum_by_date.get(date, 0)
        if pool != allocated:
            raise RuntimeError(
                f"exemption check: {date} pool {pool}c != distributed {allocated}c "
                f"(redistribution leak on an exempt day)"
            )

    # (3) classify each exempt employee by their WORKED vs EXEMPT days.
    pcol = _header_resolver(tip_alloc_period_values[0], "exemption check (period)")
    pi_emp = pcol("employee", "employee_name")
    pi_calc = pcol("our_calc", "our_total")
    pi_hours = pcol("hours_worked", "hours")
    pwidth = max(pi_emp, pi_calc, pi_hours)
    our_calc_by_emp: dict[str, int] = {}
    hours_by_emp: dict[str, float] = {}
    total_our_calc = 0
    for row in tip_alloc_period_values[1:]:
        if not row or len(row) <= pwidth or not str(row[pi_emp]).strip():
            continue
        emp = str(row[pi_emp]).strip()
        calc = _to_cents(row[pi_calc])
        our_calc_by_emp[emp] = our_calc_by_emp.get(emp, 0) + calc
        try:
            hours_by_emp[emp] = float(str(row[pi_hours]).replace(",", "") or 0)
        except ValueError:
            pass
        total_our_calc += calc

    exempt_emps = {e for (e, _d) in exempt_shifts}
    whole_period_exempt: list[str] = []
    partial_exempt: list[str] = []
    for emp in sorted(exempt_emps):
        worked = {d for (e, d) in worked_hours if e == emp}
        ex = {d for (e, d) in exempt_shifts if e == emp}
        non_exempt_worked = worked - ex
        if not non_exempt_worked:
            whole_period_exempt.append(emp)
            if our_calc_by_emp.get(emp, 0) != 0:
                raise RuntimeError(
                    f"exemption check: {emp} is exempt for every worked day but "
                    f"tip_alloc_period.our_calc={our_calc_by_emp.get(emp)}c (expected 0)"
                )
        else:
            partial_exempt.append(emp)
            if our_calc_by_emp.get(emp, 0) <= 0:
                raise RuntimeError(
                    f"exemption check: {emp} worked non-exempt days too but "
                    f"tip_alloc_period.our_calc={our_calc_by_emp.get(emp, 0)}c (expected > 0)"
                )
            expected_hours = sum(worked_hours[(emp, d)] for d in non_exempt_worked)
            got_hours = hours_by_emp.get(emp)
            if got_hours is not None and abs(got_hours - expected_hours) > 0.1:
                raise RuntimeError(
                    f"exemption check: {emp} period hours_worked={got_hours} != "
                    f"non-exempt worked hours {round(expected_hours, 2)} "
                    f"(exempt-day hours not dropped from the denominator)"
                )

    # (4) period-level conservation: total allocated == total of daily pools.
    total_pool = sum(pool_by_date.values())
    if total_our_calc != total_pool:
        raise RuntimeError(
            f"exemption check: period our_calc total {total_our_calc}c != "
            f"sum of daily day_pool {total_pool}c (period not conserved)"
        )

    return {
        "exempt_shifts_checked": len(exempt_shifts),
        "worked_exempt_shifts_dropped": verified_worked_exempt,
        "whole_period_exempt": sorted(whole_period_exempt),
        "partial_exempt": sorted(partial_exempt),
        "exempt_days_redistributed": sorted(exempt_dates),
        "period_our_calc_cents": total_our_calc,
        "period_pool_cents": total_pool,
    }


def run_e2e(
    *,
    store: str,
    pr_number: int,
    start: datetime.date,
    end: datetime.date,
    teardown_after: bool = True,
    expect_kds: bool = False,
    source: str = "gcs-replay",
    evidence_file: str | None = None,
) -> dict:
    """Full provision -> seed -> model -> verify -> teardown loop.

    ``source``:
      - ``gcs-replay`` (default): restore the GCS scrape cache for the window and
        re-parse it into the sandbox raw sheets (lenient verify — small window).
      - ``prod-raw``: read the PROD raw Square+ADP sheets directly for the window
        and write them into the sandbox raw sheets (stricter, full-period verify
        + tip-pool conservation). No scrape, no OTP, read-prod/write-sandbox only.

    Teardown always runs (finally) unless ``teardown_after`` is False, so a
    failed run never leaks sandbox sheets.
    """
    report: dict = {
        "store": store,
        "pr_number": pr_number,
        "source": source,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "days": len(dates_in_window(start, end)),
        "status": "error",
    }
    try:
        prov = sandbox_provision.provision(store=store, pr_number=pr_number)
        ids = prov["ids"]
        report["sandbox_ids"] = ids
        report["slot"] = prov.get("slot")
        report["seed_counts"] = prov.get("seed_counts")
        _apply_staging_env(ids)

        profile = sandbox_provision._load_pointer(store)
        account = profile.get("google_account_key", store)
        token = refresh_access_token(account=account)

        if source == "prod-raw":
            report["seeded_rows"] = seed_sandbox_raw_from_prod(
                profile=profile, account=account, start=start, end=end,
            )
            # Mirror the human-owned tip-exemption overlay so the sandbox model
            # build applies the SAME exemptions as prod (proves the exemption,
            # not just conservation).
            overlay_records = seed_sandbox_training_shifts_from_prod(
                profile=profile, account=account, start=start, end=end,
            )
            report["seeded_training_shifts"] = len(overlay_records)
            report["_exempt_shifts"] = {
                (r["employee_name"], r["date"]) for r in overlay_records
            }
        else:
            report["restored_files"] = _replay_from_gcs(start, end)
            _run_backfill(store, start, end)
            report["item_lines_ran"] = _maybe_run_item_lines(store)
        _run_model_build(store)

        min_rows = PROD_RAW_VERIFY_MIN_ROWS if source == "prod-raw" else SANDBOX_E2E_VERIFY_MIN_ROWS
        counts = _read_model_tab_counts(token, ids["bhaga_model"], tabs=list(min_rows))
        report["model_tab_counts"] = counts
        assert_model_tabs_populated(
            tab_row_counts=counts,
            expect_kds=expect_kds,
            min_rows=min_rows,
        )
        if source == "prod-raw":
            grids = _read_model_grids(
                token, ids["bhaga_model"], ["tip_alloc_daily", "tip_alloc_period"],
            )
            grid = grids["tip_alloc_daily"]
            period_grid = grids["tip_alloc_period"]
            report["tip_pool_conservation"] = assert_tip_pool_conserved(grid)
            report["adp_reconciliation"] = assert_adp_reconciliation_present(period_grid)
            worked_hours = _read_worked_hours(
                ids["bhaga_adp_raw"], account=account, start=start, end=end,
            )
            report["exemptions"] = assert_exemptions_applied(
                grid, period_grid, report.pop("_exempt_shifts", set()), worked_hours,
            )
        report["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if teardown_after:
            report["teardown"] = sandbox_provision.teardown(store=store, pr_number=pr_number)
        evidence = format_evidence(report)
        print(evidence)
        # Write the evidence file HERE (not in main) so it exists even on the
        # failure path — run_e2e re-raises on error, so a write in main() would
        # be skipped exactly when the diagnostic (the `error:` line) matters
        # most. The CI "Post evidence comment" step (if: always()) then has a
        # file to post on both pass and fail.
        if evidence_file:
            try:
                with open(evidence_file, "w") as fh:
                    fh.write(evidence + "\n")
            except OSError as exc:  # don't mask the real failure with an I/O error
                print(f"# WARN: could not write evidence file {evidence_file}: {exc}")
    return report


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--pr-number", type=int, required=True)
    cli.add_argument("--start", default=None, help="YYYY-MM-DD (inclusive). Omit with --auto-window.")
    cli.add_argument("--end", default=None, help="YYYY-MM-DD (inclusive). Omit with --auto-window.")
    cli.add_argument("--auto-window", action="store_true",
                     help="Auto-select up to the last --max-days *cached* dates from GCS "
                          "(deterministic + always cache-backed; preferred in CI).")
    cli.add_argument("--max-days", type=int, default=2,
                     help="Max number of most-recent cached days to replay for --auto-window "
                          "(the calendar span may be wider if the cache is sparse). Kept small for cost.")
    cli.add_argument("--keep", action="store_true",
                     help="Do NOT tear down the sandbox sheets after the run (debugging).")
    cli.add_argument("--expect-kds", action="store_true",
                     help="Also assert KDS-specific model invariants (cache must contain KDS).")
    cli.add_argument("--evidence-file", default=None,
                     help="Write the evidence block to this path (e.g. for a PR comment).")
    cli.add_argument("--source", choices=["gcs-replay", "prod-raw"], default="gcs-replay",
                     help="gcs-replay (default): re-parse the GCS scrape cache into sandbox raw. "
                          "prod-raw: read the PROD raw sheets directly for the window (no scrape/OTP) "
                          "and verify the full closed period incl. tip-pool conservation.")
    cli.add_argument("--period", choices=["last-closed"], default=None,
                     help="Resolve the window from the store's pay-period calendar instead of "
                          "--start/--end. 'last-closed' = the most recent fully-elapsed pay period.")
    args = cli.parse_args(argv)

    if args.period == "last-closed":
        profile = sandbox_provision._load_pointer(args.store)
        today = datetime.datetime.now(CT).date()
        start, end = update_model_sheet.most_recent_closed_period(
            anchor_end_date=profile["adp_run"]["pay_periods_anchor_end_date"],
            pay_frequency=profile["adp_run"].get("pay_frequency", ""),
            today=today,
        )
        print(f"# resolved last-closed pay period (today={today}): {start} -> {end}")
    elif args.auto_window:
        start, end = select_window(gcs_cache.list_cached_dates(), args.max_days)
        print(f"# auto-selected window from GCS cache: {start} -> {end}")
    elif args.start and args.end:
        start = datetime.date.fromisoformat(args.start)
        end = datetime.date.fromisoformat(args.end)
    else:
        cli.error("provide --start and --end, --auto-window, or --period last-closed")
    report = run_e2e(
        store=args.store,
        pr_number=args.pr_number,
        start=start,
        end=end,
        teardown_after=not args.keep,
        expect_kds=args.expect_kds,
        source=args.source,
        evidence_file=args.evidence_file,
    )
    print("# report:")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
