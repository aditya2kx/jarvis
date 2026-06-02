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
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import backfill_from_downloads, gcs_cache, sandbox_provision, update_model_sheet  # noqa: E402
from agents.bhaga.scripts.daily_refresh import CT, MODEL_VERIFY_MIN_ROWS, assert_model_tabs_populated  # noqa: E402
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
    cons = report.get("tip_pool_conservation")
    if cons:
        lines.append(f"- tip-pool conserved: {cons['dates_checked']} day(s), "
                     f"max residual {cons['max_residual_cents']}c")
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


def _read_model_grid(token: str, model_sid: str, tab: str) -> list[list]:
    """Read a full tab grid (header + rows) for a value-level assertion."""
    rng = f"{tab}!A1:ZZ100000"
    by_range = sandbox_provision._batch_read_values(token, model_sid, [rng])
    return by_range.get(rng, [])


def _to_cents(cell: object) -> int:
    """Parse a dollars cell ('$1,234.56' / '1234.56' / 1234.56) to integer cents."""
    s = str(cell or "").strip().replace("$", "").replace(",", "")
    if not s:
        return 0
    return int((Decimal(s) * 100).to_integral_value())


def assert_tip_pool_conserved(tip_alloc_daily_values: list[list], *, tol_cents: int = 0) -> dict:
    """Per-day conservation: sum of tip_allocation_dollars == that day's pool.

    The allocator is cent-exact (largest-remainder), so for every date the
    per-employee allocations must sum to the day's tip pool **exactly**. The
    default tolerance is therefore 0 — a 1¢/day leak must fail this gate, not
    pass silently. (Verified: real prod data rebuilds at max residual 0¢.) This
    guards against a builder bug silently dropping/duplicating cents on the way
    to the sheet.

    tip_alloc_daily columns (per the model builder):
        date | dow | period_start | period_end | employee | hours_worked |
        day_pool | team_hours_eligible | pct_of_day_hours | our_share

    Column names are resolved with fallbacks so a future header rename doesn't
    silently disable the check. Returns {dates_checked, max_residual_cents};
    raises RuntimeError if any date's residual exceeds ``tol_cents``.
    """
    if not tip_alloc_daily_values or len(tip_alloc_daily_values) < 2:
        raise RuntimeError("tip pool conservation: tip_alloc_daily is empty")
    header = [str(c).strip() for c in tip_alloc_daily_values[0]]

    def _col(*candidates: str) -> int:
        for name in candidates:
            if name in header:
                return header.index(name)
        raise RuntimeError(
            f"tip pool conservation: none of {candidates} in header {header}"
        )

    i_date = _col("date", "date_local")
    i_pool = _col("day_pool", "tip_pool_dollars")
    i_alloc = _col("our_share", "tip_allocation_dollars")

    pool_by_date: dict[str, int] = {}
    alloc_by_date: dict[str, int] = {}
    needed_cols = max(i_date, i_pool, i_alloc)
    for row in tip_alloc_daily_values[1:]:
        if not row:
            continue
        # Enforce width before indexing any resolved column: a truncated row
        # would otherwise default pool/alloc to 0 and pass the check trivially
        # (0 == 0) — a silent false-negative is exactly what this gate must not
        # do, so a short row is a hard schema regression.
        if len(row) <= needed_cols:
            raise RuntimeError(
                f"tip pool conservation: row {row!r} is too short "
                f"(need cols up to index {needed_cols}, got {len(row)})"
            )
        # Skip on the resolved date column (not a hardcoded index) so the check
        # stays correct if the header is ever reordered.
        if not str(row[i_date]).strip():
            continue
        date = str(row[i_date]).strip()[:10]
        # Pool is constant per date by construction; assert it (a per-date
        # day_pool that disagrees row-to-row is a builder bug we want surfaced,
        # not washed out in the residual). Allocations sum across employees.
        row_pool = _to_cents(row[i_pool])
        if date in pool_by_date and pool_by_date[date] != row_pool:
            raise RuntimeError(
                f"tip pool conservation: inconsistent day_pool for {date}: "
                f"{pool_by_date[date]}c vs {row_pool}c (builder bug)"
            )
        pool_by_date.setdefault(date, row_pool)
        alloc_by_date[date] = alloc_by_date.get(date, 0) + _to_cents(row[i_alloc])

    if not pool_by_date:
        raise RuntimeError(
            "tip pool conservation: no parseable date rows found in tip_alloc_daily "
            "(all rows skipped — possible date-column format change)"
        )
    problems: list[str] = []
    max_residual = 0
    for date, pool in sorted(pool_by_date.items()):
        residual = abs(alloc_by_date.get(date, 0) - pool)
        max_residual = max(max_residual, residual)
        if residual > tol_cents:
            problems.append(
                f"{date}: allocations {alloc_by_date.get(date, 0)}c != pool {pool}c "
                f"(residual {residual}c)"
            )
    if problems:
        raise RuntimeError("tip pool NOT conserved: " + "; ".join(problems))
    return {"dates_checked": len(pool_by_date), "max_residual_cents": max_residual}


def run_e2e(
    *,
    store: str,
    pr_number: int,
    start: datetime.date,
    end: datetime.date,
    teardown_after: bool = True,
    expect_kds: bool = False,
    source: str = "gcs-replay",
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
            grid = _read_model_grid(token, ids["bhaga_model"], "tip_alloc_daily")
            report["tip_pool_conservation"] = assert_tip_pool_conserved(grid)
        report["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if teardown_after:
            report["teardown"] = sandbox_provision.teardown(store=store, pr_number=pr_number)
        print(format_evidence(report))
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
    )
    if args.evidence_file:
        with open(args.evidence_file, "w") as f:
            f.write(format_evidence(report) + "\n")
    print("# report:")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
