#!/usr/bin/env python3
"""agents/bhaga/scripts/model_semantics - pure semantic post-conditions.

Single source of truth for the *semantic* correctness of the rebuilt BHAGA
model sheet, shared by:

  * ``sandbox_e2e.py``   — the mandatory per-PR CI gate (prod-raw verify), and
  * ``daily_refresh.py`` — the nightly prod pipeline guard.

Both enforce the SAME invariants so a regression cannot pass one and fail the
other. These are deliberately PURE functions (grids in, summary dict out,
``RuntimeError`` on violation): no I/O, no heavy imports, and — critically — no
import of ``daily_refresh`` / ``update_model_sheet`` (which import each other),
so this module can be imported from anywhere without a cycle.

Motivation: commit 6f87f9c silently killed the ``adp_paid``/``diff``/``diff_pct``
reconciliation columns (and the sibling fix 4059604 hit ``review_bonus_period``)
yet every MECHANICAL guard (row counts, ``data_window_end`` advanced) still
passed. These semantic checks are what would have caught it the same night.
"""

from __future__ import annotations

from decimal import Decimal


def _to_cents(cell: object) -> int:
    """Parse a dollars cell ('$1,234.56' / '1234.56' / 1234.56) to integer cents."""
    s = str(cell or "").strip().replace("$", "").replace(",", "")
    if not s:
        return 0
    return int((Decimal(s) * 100).to_integral_value())


def _header_resolver(header: list[str], what: str):
    """Return a ``_col(*candidates)`` mapping the first present name to its index."""
    norm = [str(c).strip() for c in header]

    def _col(*candidates: str) -> int:
        for name in candidates:
            if name in norm:
                return norm.index(name)
        raise RuntimeError(f"{what}: none of {candidates} in header {norm}")

    return _col


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


def _closed_period_rows(tip_alloc_period_values: list[list]):
    """Yield (period_start, period_end, adp_paid_cell) for CLOSED period rows."""
    col = _header_resolver(tip_alloc_period_values[0], "adp reconciliation")
    i_ps, i_pe = col("period_start"), col("period_end")
    i_open = col("is_open")
    i_adp = col("adp_paid")
    width = max(i_ps, i_pe, i_open, i_adp)
    for row in tip_alloc_period_values[1:]:
        if not row or len(row) <= width:
            continue
        if str(row[i_open]).strip().lower() == "yes":
            continue
        yield (
            str(row[i_ps]).strip().lstrip("'"),
            str(row[i_pe]).strip().lstrip("'"),
            str(row[i_adp]).strip(),
        )


def assert_adp_reconciliation_present(tip_alloc_period_values: list[list]) -> dict:
    """The adp_paid/diff reconciliation view must be ALIVE (regression guard).

    Commit 6f87f9c stubbed ``actual_cc_tips_by_period(None)``, leaving
    ``adp_paid``/``diff``/``diff_pct`` permanently ``"N/A"`` for every closed
    period — and CI BLESSED it (the old fixtures asserted ``"N/A"`` was correct).
    This gate asserts the MOST-RECENT closed period (which is within the GCS
    Earnings-cache era, so a covering export must exist) has a populated, numeric
    ``adp_paid`` — i.e. the cloud-native earnings load actually reconciled.

    Older closed periods predating the cache (~2026-05-29) may legitimately stay
    ``"N/A"`` (no export in GCS), so only the latest closed period is required to
    reconcile. Returns ``{closed_periods, reconciled_periods, latest_period}``;
    raises ``RuntimeError`` if the latest closed period is still ``"N/A"``.
    """
    if not tip_alloc_period_values or len(tip_alloc_period_values) < 2:
        raise RuntimeError("adp reconciliation: tip_alloc_period is empty")
    closed = list(_closed_period_rows(tip_alloc_period_values))
    if not closed:
        raise RuntimeError(
            "adp reconciliation: no closed-period rows in tip_alloc_period"
        )
    latest_end = max(pe for _, pe, _ in closed)
    latest_rows = [adp for _, pe, adp in closed if pe == latest_end]
    na = [adp for adp in latest_rows if adp in ("", "N/A")]
    if na:
        raise RuntimeError(
            f"adp reconciliation DEAD for latest closed period (end={latest_end}): "
            f"{len(na)}/{len(latest_rows)} rows have adp_paid='N/A' — the earnings "
            f"load is not wired (regression of commit 6f87f9c)"
        )
    reconciled = {pe for _, pe, adp in closed if adp not in ("", "N/A")}
    return {
        "closed_periods": len({pe for _, pe, _ in closed}),
        "reconciled_periods": len(reconciled),
        "latest_period": latest_end,
    }


def assert_period_reconciled(
    tip_alloc_period_values: list[list], period_key: tuple[str, str]
) -> dict:
    """Assert a SPECIFIC closed period reconciled (adp_paid populated).

    Used by the nightly guard, which independently knows (from the GCS cache)
    that a covering Earnings export exists for ``period_key`` = (start, end) and
    therefore the model MUST have populated adp_paid for it. Distinct from
    ``assert_adp_reconciliation_present`` (which infers the latest period from
    the grid) because the nightly can be cadence-safe: it only requires the
    period whose export it actually found.
    """
    if not tip_alloc_period_values or len(tip_alloc_period_values) < 2:
        raise RuntimeError("adp reconciliation: tip_alloc_period is empty")
    ps_want, pe_want = period_key
    rows = [
        adp for ps, pe, adp in _closed_period_rows(tip_alloc_period_values)
        if ps == ps_want and pe == pe_want
    ]
    if not rows:
        raise RuntimeError(
            f"adp reconciliation: period {ps_want}..{pe_want} has a covering "
            f"Earnings export in GCS but NO closed rows in tip_alloc_period "
            f"(period derivation regression?)"
        )
    na = [adp for adp in rows if adp in ("", "N/A")]
    if na:
        raise RuntimeError(
            f"adp reconciliation DEAD for {ps_want}..{pe_want}: {len(na)}/{len(rows)} "
            f"rows have adp_paid='N/A' even though a covering Earnings export "
            f"exists in GCS — the earnings load is not wired (regression of 6f87f9c)"
        )
    return {"period": pe_want, "rows_reconciled": len(rows)}


def assert_review_bonus_present(review_bonus_values: list[list]) -> dict:
    """Review bonuses must land when reviews were credited this run.

    Caller gates this on ``reviews_credited`` (process_reviews actually ran and
    succeeded), so an empty ``review_bonus_period`` tab here means the rebuild
    silently dropped the credited bonuses — the 4059604 bug class. Requires at
    least one data row (beyond the header).
    """
    data_rows = max(len(review_bonus_values or []) - 1, 0)
    if data_rows < 1:
        raise RuntimeError(
            "review bonuses: process_reviews ran and credited reviews this run, "
            "but review_bonus_period has 0 data rows — bonuses were silently "
            "dropped from the rebuild (regression of 4059604)"
        )
    return {"review_bonus_rows": data_rows}


def assert_model_semantics(
    *,
    tip_alloc_daily_values: list[list],
    tip_alloc_period_values: list[list],
    review_bonus_values: list[list] | None,
    require_adp_period: tuple[str, str] | None = None,
    reviews_credited: bool = False,
) -> dict:
    """Run all semantic post-conditions for the nightly prod guard.

    Cadence-safe by construction:
      * tip-pool conservation — ALWAYS checked (no cadence dependency).
      * adp reconciliation — only when ``require_adp_period`` is provided (the
        caller found a covering Earnings export in GCS for that closed period).
      * review bonuses — only when ``reviews_credited`` (process_reviews ran).

    Returns a per-check summary dict; raises ``RuntimeError`` on any violation.
    """
    report: dict = {}
    report["tip_pool_conservation"] = assert_tip_pool_conserved(tip_alloc_daily_values)
    if require_adp_period is not None:
        report["adp_reconciliation"] = assert_period_reconciled(
            tip_alloc_period_values, require_adp_period
        )
    if reviews_credited:
        report["review_bonus"] = assert_review_bonus_present(review_bonus_values or [])
    return report
