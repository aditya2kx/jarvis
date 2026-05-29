#!/usr/bin/env python3
"""BHAGA labor_daily_forecast — a LIVE, formula-driven planning worksheet.

Unlike the old static build (Python computed every cell), this writes the
forecast tab as a spreadsheet the operator can actually plan in: a few INPUT
cells per row (orders, fulltime_hours, target_labor_pct, forecast_exclude,
notes) plus a row of rate CONSTANTS, and every DERIVED column is a Google
Sheets FORMULA (=...) so editing an input recalculates sales, items, the
staffing solver and the budget/coverage flag IN THE SHEET.

The tab is FREEZE-IN-PLACE: it holds a trailing window of recent FROZEN past
forecast days (captured as VALUES the day they roll from future → past, so we
score the forecast we actually made, not a hindsight re-forecast) followed by
the live FUTURE days. See build_labor_daily_forecast_rows for the mechanics.

Layout (one row per forecast day = [frozen past window] + [~14 future days]):

  INPUTS (values, operator-editable on FUTURE rows; frozen on PAST rows)
    date | dow | orders | fulltime_hours | target_labor_pct |
    target_hourly_labor_pct | target_time_per_item_sec | forecast_exclude | notes
  HELPER CONSTANTS (values; hidden in-sheet)
    _avg_order_price | _avg_items_per_order | _avg_discount_per_order |
    _avg_tip_pool_per_order | _avg_hourly_wage |
    _shift_hours | _min_parttimers

  target_time_per_item_sec is the flat staffing-solver target (seconds/item),
  seeded from config (default 420 = 7 min) on every row but editable PER ROW;
  efficiency_hours references it. target_hourly_labor_pct is the hourly (part-
  time-only) labor% target, seeded from config (default 0.20 = 20%) but editable
  PER ROW. Operator edits to both are preserved across rebuilds (same mechanism
  as forecast_exclude / orders preservation in labor_daily).
  DERIVED (FORMULAS on FUTURE rows, evaluate live via USER_ENTERED; VALUES on
  frozen PAST rows)
    net_sales | discounts | gross_sales | items_sold | tip_pool |
    min_coverage_hours | efficiency_hours | needed_hours | fulltime_cost |
    budget_hours | recommended_hourly_hours | hourly_cost |
    total_labor_cost | actual_labor_pct | hourly_labor_pct |
    staffing_flag | hourly_staffing_flag

  actual_labor_pct = total_labor_cost / net_sales (ALL labor incl. Lindsay's
  full-time cost); hourly_labor_pct = hourly_cost / net_sales (PART-TIME labor
  ONLY, excludes Lindsay). staffing_flag keys off the total-labor budget;
  hourly_staffing_flag keys off target_hourly_labor_pct.
  ACCURACY (Python-backfilled once a forecast day has a realized actual)
    forecast_generated_at | orders_error_pct | items_sold_error_pct |
    net_sales_error_pct | fulltime_hours_error_pct | hourly_hours_error_pct |
    avg_order_price_error_pct | realized_labor_pct | total_hourly_labor_pct |
    forecast_mape

  realized_labor_pct = actual total_labor_cost / actual net_sales (ALL labor);
  total_hourly_labor_pct = actual hourly_labor_cost / actual net_sales (the
  REALIZED part-time-only counterpart, the actual analogue of the live
  hourly_labor_pct formula).

Public API:
    build_labor_daily_forecast_rows(...)   -> grid (header + formula rows)
    backfill_forecast_errors(...)          -> fill accuracy cols
    forecast_orders_dow_trend(...)         -> the order seed (excludes
                                              forecast_exclude=TRUE days)
    compute_forecast_constants(...)        -> the rate constants (pure)
    compute_staffing(...)                  -> Python mirror of the sheet
                                              solver formulas (pure, testable)
"""

from __future__ import annotations

import datetime
import statistics
import sys
import os
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from skills.bhaga_config.dates import _iso_date_for_sheet_cell, coerce_iso_date


# ── Column layout ─────────────────────────────────────────────────

FORECAST_COLUMNS: list[str] = [
    # inputs
    "date", "dow", "orders", "fulltime_hours", "target_labor_pct",
    "target_hourly_labor_pct",
    "target_time_per_item_sec", "forecast_exclude", "notes",
    # helper constants (hidden)
    "_avg_order_price", "_avg_items_per_order", "_avg_discount_per_order",
    "_avg_tip_pool_per_order", "_avg_hourly_wage",
    "_shift_hours", "_min_parttimers",
    # derived (formulas)
    "net_sales", "discounts", "gross_sales", "items_sold", "tip_pool",
    "min_coverage_hours", "efficiency_hours", "needed_hours",
    "fulltime_cost", "budget_hours", "recommended_hourly_hours",
    "hourly_cost", "total_labor_cost", "actual_labor_pct", "hourly_labor_pct",
    "staffing_flag", "hourly_staffing_flag",
    # accuracy (python-backfilled)
    "forecast_generated_at",
    "orders_error_pct", "items_sold_error_pct", "net_sales_error_pct",
    "fulltime_hours_error_pct", "hourly_hours_error_pct",
    "avg_order_price_error_pct", "realized_labor_pct", "total_hourly_labor_pct",
    "forecast_mape",
]

# Number of trailing PAST calendar days kept FROZEN in the forecast tab so
# their forecast-vs-actual error can be scored once the actual lands. Small,
# easy to tune. Frozen rows older than this (relative to the horizon start) are
# dropped so the tab doesn't grow unbounded.
FREEZE_WINDOW_DAYS: int = 30

_IDX: dict[str, int] = {name: i for i, name in enumerate(FORECAST_COLUMNS)}

# Helper-constant columns get hidden in-sheet (they exist only so the derived
# formulas have stable cell references). 0-based indices.
FORECAST_HIDDEN_COLS: list[int] = [
    _IDX[name] for name in (
        "_avg_order_price", "_avg_items_per_order", "_avg_discount_per_order",
        "_avg_tip_pool_per_order", "_avg_hourly_wage",
        "_shift_hours", "_min_parttimers",
    )
]

# Currency columns (0-based) for the forecast tab — used by the writer's
# format_currency_columns pass. (Percent columns are auto-detected by the
# writer via the "pct"/"mape" name heuristic, so they're not listed here.)
FORECAST_CURRENCY_COLS: list[int] = [
    _IDX["_avg_order_price"], _IDX["_avg_discount_per_order"],
    _IDX["_avg_tip_pool_per_order"], _IDX["_avg_hourly_wage"],
    _IDX["net_sales"], _IDX["discounts"], _IDX["gross_sales"],
    _IDX["tip_pool"], _IDX["fulltime_cost"], _IDX["hourly_cost"],
    _IDX["total_labor_cost"],
]


def _col_letter(idx0: int) -> str:
    """0-based column index → A1 column letter (0→A, 25→Z, 26→AA)."""
    s = ""
    n = idx0
    while True:
        n, r = divmod(n, 26)
        s = chr(ord("A") + r) + s
        if n == 0:
            break
        n -= 1
    return s


def _ref(name: str, sheet_row: int) -> str:
    """A1 reference to column `name` in `sheet_row` (e.g. ('orders', 2) → 'C2')."""
    return f"{_col_letter(_IDX[name])}{sheet_row}"


def _derived_formulas(sheet_row: int) -> dict[str, str]:
    """All derived-column formula strings for one sheet row.

    Cross-references use the row's own input + helper cells, so editing any
    input recalculates the whole chain. budget_hours is the hours of hourly
    labor the labor budget allows AFTER full-time (Lindsay); recommended hours
    stay = needed_hours (coverage/efficiency floor) — budget is a CHECK
    surfaced by staffing_flag, never a cap that would understaff below
    coverage.
    """
    r = sheet_row
    R = lambda n: _ref(n, r)  # noqa: E731
    return {
        "net_sales": f"={R('orders')}*{R('_avg_order_price')}",
        "discounts": f"={R('orders')}*{R('_avg_discount_per_order')}",
        "gross_sales": f"={R('net_sales')}+{R('discounts')}",
        "items_sold": f"={R('orders')}*{R('_avg_items_per_order')}",
        "tip_pool": f"={R('orders')}*{R('_avg_tip_pool_per_order')}",
        "min_coverage_hours": f"={R('_min_parttimers')}*{R('_shift_hours')}",
        "efficiency_hours": f"={R('items_sold')}*{R('target_time_per_item_sec')}/3600",
        "needed_hours": f"=MAX({R('min_coverage_hours')},{R('efficiency_hours')})",
        "fulltime_cost": f"={R('fulltime_hours')}*{R('_avg_hourly_wage')}",
        "budget_hours": (
            f"=({R('target_labor_pct')}*{R('net_sales')}-{R('fulltime_cost')})"
            f"/{R('_avg_hourly_wage')}"
        ),
        "recommended_hourly_hours": f"={R('needed_hours')}",
        "hourly_cost": f"={R('recommended_hourly_hours')}*{R('_avg_hourly_wage')}",
        "total_labor_cost": f"={R('hourly_cost')}+{R('fulltime_cost')}",
        "actual_labor_pct": f"=IF({R('net_sales')}>0,{R('total_labor_cost')}/{R('net_sales')},0)",
        # Hourly (part-time-only) labor% — excludes Lindsay's full-time cost.
        "hourly_labor_pct": f"=IF({R('net_sales')}>0,{R('hourly_cost')}/{R('net_sales')},0)",
        "staffing_flag": (
            f"=IF({R('needed_hours')}>{R('budget_hours')},\"BUDGET_CONFLICT\","
            f"IF({R('budget_hours')}>{R('needed_hours')}*1.25,"
            f"\"OVERSTAFFED_BUDGET\",\"OK\"))"
        ),
        # Mirror of staffing_flag keyed to the hourly labor% target: OVER when
        # hourly_labor_pct exceeds target_hourly_labor_pct, UNDER when it sits
        # well below (lots of headroom), OK in between.
        "hourly_staffing_flag": (
            f"=IF({R('hourly_labor_pct')}>{R('target_hourly_labor_pct')},"
            f"\"OVER_HOURLY_BUDGET\","
            f"IF({R('hourly_labor_pct')}<{R('target_hourly_labor_pct')}*0.75,"
            f"\"UNDER_HOURLY_BUDGET\",\"OK\"))"
        ),
    }


# ── labor_daily parsing ───────────────────────────────────────────


def _parse_daily_row(row: list, header: list[str]) -> dict[str, Any] | None:
    """Parse a labor_daily row into a keyed dict. Returns None for unparseable rows."""
    if len(row) < 8:
        return None
    date_raw = coerce_iso_date(row[0])
    if date_raw is None:
        return None

    def _float(v):
        if v == "" or v is None:
            return 0.0
        s = str(v).rstrip("%")
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    def _int(v):
        if v == "" or v is None:
            return 0
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0

    # forecast_exclude lives in an appended column; locate by name so we don't
    # depend on its absolute position (older sheets won't have it at all).
    forecast_exclude = False
    if "forecast_exclude" in header:
        fe_i = header.index("forecast_exclude")
        if fe_i < len(row):
            forecast_exclude = str(row[fe_i]).strip().lower() in ("true", "1", "yes", "y", "t")

    return {
        "date": date_raw,
        "dow": datetime.date.fromisoformat(date_raw).weekday(),
        "gross_sales": _float(row[2]),
        "discounts": _float(row[3]),
        "net_sales": _float(row[4]),
        "tip_pool": _float(row[5]),
        "net_sales_plus_tips": _float(row[6]),
        "orders": _int(row[7]),
        "hourly_hours": _float(row[8]),
        "hourly_labor_cost": _float(row[9]),
        "fulltime_hours": _float(row[10]),
        "fulltime_labor_cost": _float(row[11]),
        "total_labor_cost": _float(row[12]),
        "items_sold": _int(row[30]) if len(row) > 30 and row[30] != "" else 0,
        "forecast_exclude": forecast_exclude,
    }


def _get_parsed_rows(
    labor_daily_rows: list[list],
    *,
    exclude_flagged: bool = True,
) -> list[dict]:
    """Parse all labor_daily rows (skip header).

    Only operating days (orders > 0) are returned. When ``exclude_flagged`` is
    True (the default for every forecast computation), days the operator (or
    the outlier detector) marked ``forecast_exclude=TRUE`` are dropped so they
    never pollute the order seed, the per-order rate constants, or the DOW
    full-time average.
    """
    if len(labor_daily_rows) <= 1:
        return []
    header = labor_daily_rows[0]
    parsed = []
    for row in labor_daily_rows[1:]:
        p = _parse_daily_row(row, header)
        if p is None or p["orders"] <= 0:
            continue
        if exclude_flagged and p["forecast_exclude"]:
            continue
        parsed.append(p)
    return parsed


# ── Order seed ────────────────────────────────────────────────────


def _expected_orders_from_records(
    records: list[dict],
    target_date: datetime.date,
    *,
    lookback_weeks: int = 6,
    decay: float = 0.8,
) -> float:
    """Weighted-DOW average × capped trend factor, evaluated for ``target_date``.

    ``records`` is a list of operating-day dicts each carrying ``date`` (ISO
    str), ``dow`` (int weekday) and ``orders`` (int). Only days STRICTLY before
    ``target_date`` feed the estimate, so evaluating this at a historical day
    yields a leakage-free expectation for that day — which is exactly what the
    trend-aware outlier detector needs.

    DOW weighted average: last ``lookback_weeks`` same-day-of-week values,
    weighted by exponential decay (most recent = 1.0, then decay, decay^2, ...).
    Trend factor: avg(last 2 weeks) / avg(prior 2 weeks), capped [0.85, 1.15] —
    this is what lets a sustained growth run be absorbed into the expectation
    instead of read as a string of upward anomalies. Returns 0.0 when there's
    no same-DOW history to judge against. The result is NOT rounded; callers
    round for display.
    """
    iso = target_date.isoformat()
    target_dow = target_date.weekday()
    prior = sorted(
        (r for r in records if r["date"] < iso),
        key=lambda x: x["date"], reverse=True,
    )
    if not prior:
        return 0.0
    same_dow = [r for r in prior if r["dow"] == target_dow][:lookback_weeks]
    if not same_dow:
        return 0.0
    weights = [decay ** i for i in range(len(same_dow))]
    dow_avg = sum(r["orders"] * w for r, w in zip(same_dow, weights)) / sum(weights)

    if len(prior) < 14:
        return dow_avg
    last_2_weeks = prior[:14]
    prior_2_weeks = prior[14:28]
    if not prior_2_weeks:
        return dow_avg
    avg_recent = statistics.mean(r["orders"] for r in last_2_weeks)
    avg_prior = statistics.mean(r["orders"] for r in prior_2_weeks)
    trend = 1.0 if avg_prior <= 0 else avg_recent / avg_prior
    trend = max(0.85, min(1.15, trend))
    return dow_avg * trend


def forecast_orders_dow_trend(
    labor_daily_rows: list[list],
    target_date: datetime.date,
    lookback_weeks: int = 6,
    decay: float = 0.8,
) -> float:
    """Forecast orders using weighted DOW average + trend factor.

    Thin wrapper over _expected_orders_from_records (the single source of truth
    for the weighted-DOW + trend expectation, shared with the outlier detector).
    Days flagged forecast_exclude=TRUE (operator override or outlier default)
    are dropped before any averaging — see _get_parsed_rows(exclude_flagged).
    """
    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=True)
    if not parsed:
        return 0.0
    records = [
        {"date": r["date"], "dow": r["dow"], "orders": r["orders"]} for r in parsed
    ]
    expected = _expected_orders_from_records(
        records, target_date, lookback_weeks=lookback_weeks, decay=decay,
    )
    return round(expected, 0)


def compute_outlier_stats(
    operating_days: list[dict],
    *,
    window_weeks: int = 8,
    z_threshold: float = 2.5,
    lookback_weeks: int = 6,
    decay: float = 0.8,
) -> dict[str, dict]:
    """Trend-aware, robust outlier detection for daily order counts.

    ``operating_days``: list of ``{"date": ISO, "orders": int}`` for days with
    orders > 0 (closed / zero-order days carry no demand signal and would
    pollute the residual dispersion, so the caller filters them out before
    calling — see build_labor_daily_rows).

    For each day the EXPECTED order count comes from the SAME weighted-DOW +
    trend model the live seed uses (_expected_orders_from_records), evaluated at
    that day's date using prior days only. Because sustained growth is absorbed
    into the expectation, a growth run produces small residuals rather than a
    string of "far from the flat average" false positives — the core bug this
    replaces.

    residual = actual - expected. Dispersion is the median + MAD of residuals
    over the trailing ``window_weeks`` (robust to a few extreme days):
        robust_z = (residual - median_residual) / (1.4826 * MAD)
    When MAD is degenerate (≥ half the residuals identical) we fall back to the
    population stdev, then to a sane floor (10% of the median expected, min 1
    order) so a near-constant series can't divide by zero.

    Returns ``{date_iso: {expected, residual, robust_z, outlier_flag,
    exclude_default}}``:
      * outlier_flag    — BOTH directions (|z| > threshold); informational, for
                          operator visibility.
      * exclude_default — DOWN only (z < -threshold AND actual < expected);
                          the auto-exclusion default for anomalous lows
                          (stock-out / early-close). Upward / growth days are
                          NEVER auto-excluded.
    """
    records = [
        {
            "date": r["date"],
            "dow": datetime.date.fromisoformat(r["date"]).weekday(),
            "orders": int(r["orders"]),
        }
        for r in operating_days
        if int(r.get("orders", 0)) > 0
    ]
    if not records:
        return {}

    expected_by_date: dict[str, float] = {}
    resid_by_date: dict[str, float] = {}
    for r in records:
        d = datetime.date.fromisoformat(r["date"])
        expected = _expected_orders_from_records(
            records, d, lookback_weeks=lookback_weeks, decay=decay,
        )
        if expected <= 0:
            continue
        expected_by_date[r["date"]] = expected
        resid_by_date[r["date"]] = r["orders"] - expected
    if not resid_by_date:
        return {}

    max_date = max(datetime.date.fromisoformat(d) for d in resid_by_date)
    window_start = (max_date - datetime.timedelta(days=window_weeks * 7)).isoformat()
    window_resids = [v for d, v in resid_by_date.items() if d >= window_start]
    if len(window_resids) < 2:
        window_resids = list(resid_by_date.values())

    median_resid = statistics.median(window_resids)
    mad = statistics.median([abs(x - median_resid) for x in window_resids])
    scale = 1.4826 * mad
    if scale <= 0:
        try:
            scale = statistics.pstdev(window_resids)
        except statistics.StatisticsError:
            scale = 0.0
        if scale <= 0:
            med_expected = statistics.median(list(expected_by_date.values()))
            scale = max(1.0, 0.10 * med_expected)

    out: dict[str, dict] = {}
    for d, residual in resid_by_date.items():
        robust_z = (residual - median_resid) / scale if scale > 0 else 0.0
        is_outlier = abs(robust_z) > z_threshold
        exclude_default = (robust_z < -z_threshold) and (residual < 0)
        out[d] = {
            "expected": round(expected_by_date[d], 1),
            "residual": round(residual, 1),
            "robust_z": round(robust_z, 2),
            "outlier_flag": is_outlier,
            "exclude_default": exclude_default,
        }
    return out


def _forecast_fulltime_hours_dow_raw(
    parsed: list[dict],
    target_date: datetime.date,
    lookback_weeks: int = 4,
) -> float:
    """Uncapped historical DOW average of full-time (Lindsay) hours.

    Weekly capping to forecast_fulltime_weekly_hours happens AFTER the whole
    horizon is built (see build_labor_daily_forecast_rows) so the cap is on the
    weekly SUM, not an artificial per-day ceiling.
    """
    target_dow = target_date.weekday()
    same_dow = [
        r for r in sorted(parsed, key=lambda x: x["date"], reverse=True)
        if r["dow"] == target_dow and r["date"] < target_date.isoformat()
    ][:lookback_weeks]
    if not same_dow:
        return 0.0
    return round(statistics.mean(r["fulltime_hours"] for r in same_dow), 2)


# ── Rate constants ────────────────────────────────────────────────


def compute_forecast_constants(
    parsed_recent: list[dict],
    *,
    avg_hourly_wage: float,
    min_parttimers: int = 2,
) -> dict[str, float]:
    """Compute the per-order rate constants from recent operating days.

    Pure function. ``parsed_recent`` is a list of parsed labor_daily dicts
    (already filtered to recent, non-excluded, operating days). Discounts are
    returned as a POSITIVE per-order magnitude so the sheet identity
    gross_sales = net_sales + discounts yields gross > net (labor_daily stores
    discounts as negative; we take abs()).

    NOTE: the staffing-solver target (target_time_per_item_sec) is no longer a
    hidden constant here — it's a VISIBLE, per-row editable input column seeded
    from config in build_labor_daily_forecast_rows.
    """
    order_prices = [r["net_sales"] / r["orders"] for r in parsed_recent if r["orders"] > 0]
    items_per_order = [
        r["items_sold"] / r["orders"] for r in parsed_recent
        if r["orders"] > 0 and r["items_sold"] > 0
    ]
    disc_per_order = [abs(r["discounts"]) / r["orders"] for r in parsed_recent if r["orders"] > 0]
    tip_per_order = [r["tip_pool"] / r["orders"] for r in parsed_recent if r["orders"] > 0]
    return {
        "_avg_order_price": round(statistics.mean(order_prices), 2) if order_prices else 0.0,
        "_avg_items_per_order": round(statistics.mean(items_per_order), 3) if items_per_order else 0.0,
        "_avg_discount_per_order": round(statistics.mean(disc_per_order), 4) if disc_per_order else 0.0,
        "_avg_tip_pool_per_order": round(statistics.mean(tip_per_order), 4) if tip_per_order else 0.0,
        "_avg_hourly_wage": round(avg_hourly_wage, 2),
        "_min_parttimers": min_parttimers,
    }


def _shift_hours_by_dow(kds_by_date: dict[str, dict] | None) -> dict[int, float]:
    """Average KDS shift-envelope hours (shift_end - shift_start) per DOW.

    Built from the raw kds_daily rows (which carry shift_start / shift_end as
    HH:MM strings). Returns {weekday: avg_hours}. DOWs with no KDS history are
    absent; the caller falls back to a sane default.
    """
    by_dow: dict[int, list[float]] = {}
    for date_iso, k in (kds_by_date or {}).items():
        start = str(k.get("shift_start", "")).strip()
        end = str(k.get("shift_end", "")).strip()
        if ":" not in start or ":" not in end:
            continue
        try:
            sh, sm = map(int, start.split(":")[:2])
            eh, em = map(int, end.split(":")[:2])
            hours = (eh * 60 + em - sh * 60 - sm) / 60.0
        except (ValueError, TypeError):
            continue
        if hours <= 0:
            continue
        try:
            wd = datetime.date.fromisoformat(date_iso).weekday()
        except (ValueError, TypeError):
            continue
        by_dow.setdefault(wd, []).append(hours)
    return {wd: round(statistics.mean(v), 2) for wd, v in by_dow.items()}


# ── Pure Python mirror of the sheet solver (for tests) ─────────────


def compute_staffing(
    *,
    orders: float,
    avg_order_price: float,
    avg_items_per_order: float,
    avg_discount_per_order: float,
    avg_tip_pool_per_order: float,
    avg_hourly_wage: float,
    target_time_per_item_sec: float,
    shift_hours: float,
    min_parttimers: float,
    fulltime_hours: float,
    target_labor_pct: float,
    target_hourly_labor_pct: float = 0.20,
) -> dict[str, float | str]:
    """Replicate the in-sheet derived formulas in Python.

    Single source of truth for the math that the spreadsheet evaluates, so the
    unit tests can assert the solver behaves as designed without a live Sheet.
    Keep this in lockstep with _derived_formulas().
    """
    net_sales = orders * avg_order_price
    discounts = orders * avg_discount_per_order
    gross_sales = net_sales + discounts
    items_sold = orders * avg_items_per_order
    tip_pool = orders * avg_tip_pool_per_order
    min_coverage_hours = min_parttimers * shift_hours
    efficiency_hours = items_sold * target_time_per_item_sec / 3600.0
    needed_hours = max(min_coverage_hours, efficiency_hours)
    fulltime_cost = fulltime_hours * avg_hourly_wage
    budget_hours = (
        (target_labor_pct * net_sales - fulltime_cost) / avg_hourly_wage
        if avg_hourly_wage else 0.0
    )
    recommended_hourly_hours = needed_hours
    hourly_cost = recommended_hourly_hours * avg_hourly_wage
    total_labor_cost = hourly_cost + fulltime_cost
    actual_labor_pct = (total_labor_cost / net_sales) if net_sales > 0 else 0.0
    hourly_labor_pct = (hourly_cost / net_sales) if net_sales > 0 else 0.0
    if needed_hours > budget_hours:
        staffing_flag = "BUDGET_CONFLICT"
    elif budget_hours > needed_hours * 1.25:
        staffing_flag = "OVERSTAFFED_BUDGET"
    else:
        staffing_flag = "OK"
    if hourly_labor_pct > target_hourly_labor_pct:
        hourly_staffing_flag = "OVER_HOURLY_BUDGET"
    elif hourly_labor_pct < target_hourly_labor_pct * 0.75:
        hourly_staffing_flag = "UNDER_HOURLY_BUDGET"
    else:
        hourly_staffing_flag = "OK"
    return {
        "net_sales": net_sales,
        "discounts": discounts,
        "gross_sales": gross_sales,
        "items_sold": items_sold,
        "tip_pool": tip_pool,
        "min_coverage_hours": min_coverage_hours,
        "efficiency_hours": efficiency_hours,
        "needed_hours": needed_hours,
        "fulltime_cost": fulltime_cost,
        "budget_hours": budget_hours,
        "recommended_hourly_hours": recommended_hourly_hours,
        "hourly_cost": hourly_cost,
        "total_labor_cost": total_labor_cost,
        "actual_labor_pct": actual_labor_pct,
        "hourly_labor_pct": hourly_labor_pct,
        "staffing_flag": staffing_flag,
        "hourly_staffing_flag": hourly_staffing_flag,
    }


# ── Existing-tab freeze capture ───────────────────────────────────


def _parse_existing_forecast_grid(
    existing_forecast_rows: list[list] | None,
) -> dict[str, dict[str, Any]]:
    """Map an existing labor_daily_forecast grid → {date_iso: {col_name: value}}.

    The grid is read straight from the sheet (header + value rows). Because the
    Sheets Values API returns the EVALUATED value of a formula cell (a number),
    every derived column already carries its computed value here — which is
    exactly what we freeze when a forecast day rolls from future to past, so we
    score the forecast we actually made rather than a hindsight re-forecast.
    Columns are mapped by HEADER NAME so this stays correct even if an older
    tab had a different column set.
    """
    out: dict[str, dict[str, Any]] = {}
    if not existing_forecast_rows or len(existing_forecast_rows) <= 1:
        return out
    header = existing_forecast_rows[0]
    if "date" not in header:
        return out
    date_i = header.index("date")
    for row in existing_forecast_rows[1:]:
        if date_i >= len(row):
            continue
        iso = coerce_iso_date(row[date_i])
        if iso is None:
            continue
        captured: dict[str, Any] = {}
        for i, name in enumerate(header):
            captured[name] = row[i] if i < len(row) else ""
        out[iso] = captured
    return out


# ── Grid builder ──────────────────────────────────────────────────


def build_labor_daily_forecast_rows(
    *,
    labor_daily_rows: list[list],
    wage_rates: list[dict],
    config: dict,
    kds_by_date: dict[str, dict] | None = None,
    existing_target_by_date: dict[str, float] | None = None,
    existing_hourly_target_by_date: dict[str, float] | None = None,
    existing_forecast_rows: list[list] | None = None,
    horizon_days: int = 14,
    freeze_window_days: int = FREEZE_WINDOW_DAYS,
) -> list[list]:
    """Build the freeze-in-place labor_daily_forecast grid (header + N rows).

    The grid is ``[frozen past rows] + [future rows]``:

    * FUTURE rows (horizon start = last_actual+1 .. +horizon_days) are LIVE:
      INPUT cells as values, HELPER constants as values, DERIVED columns as
      ``=...`` formula strings (the writer uses USER_ENTERED so they evaluate
      live), ACCURACY columns blank.
    * FROZEN past rows are captured from ``existing_forecast_rows`` (read from
      the sheet, so formula cells carry their evaluated VALUES) the moment a
      date rolls from future → past. They are written as VALUES (no live
      formulas, no operator-editable inputs) with their ORIGINAL
      forecast_generated_at preserved, so backfill_forecast_errors can score
      the forecast we actually made against the realized actual. Frozen rows
      older than ``freeze_window_days`` before the horizon start are dropped so
      the tab can't grow unbounded.

    ``target_time_per_item_sec`` (config default 420) and
    ``target_hourly_labor_pct`` (config default 0.20) are seeded per FUTURE
    row, but any operator edit for a date present in ``existing_target_by_date``
    / ``existing_hourly_target_by_date`` is PRESERVED across rebuilds.
    """
    target_labor_pct = float(config.get("forecast_target_labor_pct", 0.25))
    target_hourly_labor_pct = float(config.get("forecast_target_hourly_labor_pct", 0.20))
    fulltime_weekly_hours = float(config.get("forecast_fulltime_weekly_hours", 40.0))
    target_time_per_item = float(config.get("forecast_target_completion_time_per_item_sec", 420.0))
    existing_target_by_date = existing_target_by_date or {}
    existing_hourly_target_by_date = existing_hourly_target_by_date or {}
    existing_by_date = _parse_existing_forecast_grid(existing_forecast_rows)

    hourly_wages = [
        float(r["wage_rate_dollars"])
        for r in wage_rates
        if r.get("wage_rate_dollars")
        and not r.get("is_salaried")
        and not r.get("excluded_from_labor_pct")
    ]
    avg_hourly_wage = statistics.mean(hourly_wages) if hourly_wages else 15.0

    header = list(FORECAST_COLUMNS)
    rows: list[list] = [header]

    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=True)
    if not parsed:
        return rows

    # Recent window (last 4 weeks of non-excluded operating days) for the rate
    # constants. Sorted desc, capped at 28 days.
    recent = sorted(parsed, key=lambda x: x["date"], reverse=True)[:28]
    constants = compute_forecast_constants(
        recent,
        avg_hourly_wage=avg_hourly_wage,
    )
    shift_hours_dow = _shift_hours_by_dow(kds_by_date)
    # Fallback shift length: median of whatever DOW envelopes we do have, else
    # 11h (Palmetto 10:00–21:00 shop hours).
    if shift_hours_dow:
        shift_hours_fallback = round(statistics.median(shift_hours_dow.values()), 2)
    else:
        shift_hours_fallback = 11.0

    # Anchor the horizon on the last CALENDAR day we actually have data for —
    # NOT the last non-excluded day. `parsed` drops forecast_exclude=TRUE rows,
    # so during a stretch where the most recent days are all flagged (e.g. a
    # hyper-growth run where sustained growth trips the DOW outlier band, or the
    # near-closed tail days), max(parsed.date) rewinds into the past and the
    # "forecast" ends up covering days that have already happened. Use the full
    # unfiltered set of operating days (orders > 0) to find the true last actual.
    all_rows = _get_parsed_rows(labor_daily_rows, exclude_flagged=False)
    operating = [r for r in all_rows if r.get("orders", 0) > 0] or all_rows
    last_actual_date = max(r["date"] for r in operating)
    start_date = datetime.date.fromisoformat(last_actual_date) + datetime.timedelta(days=1)
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    # First pass: compute per-day input + helper values; stash raw full-time
    # hours so we can apply the WEEKLY cap before emitting.
    staged: list[dict] = []
    for i in range(horizon_days):
        target_date = start_date + datetime.timedelta(days=i)
        wd = target_date.weekday()
        orders = int(forecast_orders_dow_trend(labor_daily_rows, target_date))
        ft_raw = _forecast_fulltime_hours_dow_raw(parsed, target_date)
        shift_hours = shift_hours_dow.get(wd, shift_hours_fallback)
        staged.append({
            "date": target_date,
            "wd": wd,
            "orders": orders,
            "ft_raw": ft_raw,
            "shift_hours": shift_hours,
        })

    # Weekly cap: scale each ISO week's full-time hours so the week SUM never
    # exceeds forecast_fulltime_weekly_hours (Lindsay's 40h ceiling).
    by_week: dict[datetime.date, list[dict]] = {}
    for s in staged:
        monday = s["date"] - datetime.timedelta(days=s["date"].weekday())
        by_week.setdefault(monday, []).append(s)
    for week_rows in by_week.values():
        week_sum = sum(s["ft_raw"] for s in week_rows)
        if week_sum > fulltime_weekly_hours and week_sum > 0:
            scale = fulltime_weekly_hours / week_sum
            for s in week_rows:
                s["ft_capped"] = round(s["ft_raw"] * scale, 2)
        else:
            for s in week_rows:
                s["ft_capped"] = round(s["ft_raw"], 2)

    # ── Freeze-in-place: carry forward recent PAST forecast rows ──────
    # Any existing-tab row whose date is now in the past (< horizon start) but
    # still within the trailing freeze window is captured AS VALUES. The Sheets
    # read already returned evaluated values for the formula cells, so we freeze
    # the forecast we actually made (no hindsight re-forecast). Rows older than
    # the window are dropped (not carried), so the tab stays bounded.
    freeze_start = start_date - datetime.timedelta(days=freeze_window_days)
    frozen_rows: list[list] = []
    for iso in sorted(existing_by_date):
        try:
            d = datetime.date.fromisoformat(iso)
        except (ValueError, TypeError):
            continue
        if d >= start_date:
            continue  # future date → will be regenerated live below
        if d < freeze_start:
            continue  # older than the trailing window → drop
        src = existing_by_date[iso]
        frozen: list[Any] = [""] * len(FORECAST_COLUMNS)
        for name in FORECAST_COLUMNS:
            val = src.get(name, "")
            # Never carry an unevaluated formula string into a frozen value
            # cell — if the source somehow still holds "=...", blank it.
            if isinstance(val, str) and val.startswith("="):
                val = ""
            frozen[_IDX[name]] = val
        # Canonicalize the date as a text literal; preserve original
        # forecast_generated_at if the source carried one.
        frozen[_IDX["date"]] = _iso_date_for_sheet_cell(iso)
        frozen_rows.append(frozen)

    # ── Future rows: live formulas, operator-editable inputs ──────────
    # Sheet row number = header(1) + frozen rows + 1-based future index, so the
    # derived-column A1 references resolve against each row's OWN cells.
    n_frozen = len(frozen_rows)
    future_rows: list[list] = []
    for i, s in enumerate(staged):
        sheet_row = i + 2 + n_frozen
        target_date = s["date"]
        formulas = _derived_formulas(sheet_row)
        row: list[Any] = [""] * len(FORECAST_COLUMNS)

        # inputs
        row[_IDX["date"]] = _iso_date_for_sheet_cell(target_date.isoformat())
        row[_IDX["dow"]] = target_date.strftime("%a")
        row[_IDX["orders"]] = s["orders"]
        row[_IDX["fulltime_hours"]] = s["ft_capped"]
        row[_IDX["target_labor_pct"]] = target_labor_pct
        # Per-row editable hourly labor% target; preserve operator edits.
        row[_IDX["target_hourly_labor_pct"]] = round(
            float(existing_hourly_target_by_date.get(
                target_date.isoformat(), target_hourly_labor_pct)), 4
        )
        # Per-row editable staffing-solver target; preserve operator edits.
        row[_IDX["target_time_per_item_sec"]] = round(
            float(existing_target_by_date.get(target_date.isoformat(), target_time_per_item)), 1
        )
        row[_IDX["forecast_exclude"]] = "FALSE"
        row[_IDX["notes"]] = ""

        # helper constants
        row[_IDX["_avg_order_price"]] = constants["_avg_order_price"]
        row[_IDX["_avg_items_per_order"]] = constants["_avg_items_per_order"]
        row[_IDX["_avg_discount_per_order"]] = constants["_avg_discount_per_order"]
        row[_IDX["_avg_tip_pool_per_order"]] = constants["_avg_tip_pool_per_order"]
        row[_IDX["_avg_hourly_wage"]] = constants["_avg_hourly_wage"]
        row[_IDX["_shift_hours"]] = s["shift_hours"]
        row[_IDX["_min_parttimers"]] = constants["_min_parttimers"]

        # derived formulas
        for name, formula in formulas.items():
            row[_IDX[name]] = formula

        # accuracy
        row[_IDX["forecast_generated_at"]] = generated_at
        # error/realized columns start blank (backfilled when actuals land)

        future_rows.append(row)

    rows.extend(frozen_rows)
    rows.extend(future_rows)
    return rows


# ── Accuracy backfill ─────────────────────────────────────────────


def backfill_forecast_errors(
    *,
    forecast_rows: list[list],
    labor_daily_rows: list[list],
) -> list[list]:
    """Fill accuracy columns for forecast rows whose date now has an actual.

    With FREEZE-IN-PLACE the forecast tab retains a trailing window of FROZEN
    past rows whose input + helper cells hold the values we forecast for that
    day. Forecasted values are recomputed in Python from the row's OWN input +
    helper cells (consistent with the in-sheet formulas, and for a frozen row
    those inputs ARE the frozen forecast). Error = (actual - forecast)/actual.

    labor_daily actuals are resolved BY HEADER NAME, not fixed positions —
    labor_daily has had columns appended over time (KDS metrics, breakdown
    cols, outlier_flag, forecast_exclude), and once freeze-in-place starts
    scoring real rows a positional drift would silently compute garbage. Name
    resolution stays correct regardless of layout, with positional fallback
    only if a header name is missing.
    """
    if len(forecast_rows) <= 1 or len(labor_daily_rows) <= 1:
        return forecast_rows

    ld_header = labor_daily_rows[0]
    # Resolve actual columns by header NAME (robust to layout drift), falling
    # back to the historical fixed positions only if a name is absent.
    _ld_idx = {str(name).strip(): i for i, name in enumerate(ld_header)}

    def _col(name: str, fallback: int) -> int:
        return _ld_idx.get(name, fallback)

    c_date = _col("date", 0)
    c_net = _col("net_sales", 4)
    c_orders = _col("orders", 7)
    c_hourly_h = _col("hourly_hours", 8)
    c_hourly_cost = _col("hourly_labor_cost", 9)
    c_ft_h = _col("fulltime_hours", 10)
    c_total_cost = _col("total_labor_cost", 12)
    c_items = _col("items_sold", 30)

    def _ld_float(row, i):
        if i >= len(row) or row[i] == "" or row[i] is None:
            return 0.0
        try:
            return float(row[i])
        except (ValueError, TypeError):
            return 0.0

    def _ld_int(row, i):
        return int(_ld_float(row, i))

    actual_by_date: dict[str, dict] = {}
    for row in labor_daily_rows[1:]:
        d = coerce_iso_date(row[c_date]) if c_date < len(row) else None
        if d is None:
            continue
        actual_by_date[d] = {
            "orders": _ld_int(row, c_orders),
            "net_sales": _ld_float(row, c_net),
            "items_sold": _ld_int(row, c_items),
            "fulltime_hours": _ld_float(row, c_ft_h),
            "hourly_hours": _ld_float(row, c_hourly_h),
            "hourly_labor_cost": _ld_float(row, c_hourly_cost),
            "total_labor_cost": _ld_float(row, c_total_cost),
        }

    header = forecast_rows[0]
    idx = {name: i for i, name in enumerate(header)}

    def _num(row, name, default=0.0):
        i = idx.get(name)
        if i is None or i >= len(row):
            return default
        v = row[i]
        if v == "" or v is None or (isinstance(v, str) and v.startswith("=")):
            return default
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    def _err(actual_val, forecast_val):
        if not actual_val:
            return ""
        return round((actual_val - forecast_val) / actual_val, 4)

    for row in forecast_rows[1:]:
        d = coerce_iso_date(row[idx["date"]]) if "date" in idx else None
        if d is None or d not in actual_by_date:
            continue
        actual = actual_by_date[d]

        f_orders = _num(row, "orders")
        f_price = _num(row, "_avg_order_price")
        f_items_per = _num(row, "_avg_items_per_order")
        f_ft = _num(row, "fulltime_hours")
        f_shift = _num(row, "_shift_hours")
        f_minpt = _num(row, "_min_parttimers")
        f_tpi = _num(row, "target_time_per_item_sec")

        f_net = f_orders * f_price
        f_items = f_orders * f_items_per
        f_eff = f_items * f_tpi / 3600.0
        f_hourly = max(f_minpt * f_shift, f_eff)

        row[idx["orders_error_pct"]] = _err(actual["orders"], f_orders)
        row[idx["items_sold_error_pct"]] = _err(actual["items_sold"], f_items)
        row[idx["net_sales_error_pct"]] = _err(actual["net_sales"], f_net)
        row[idx["fulltime_hours_error_pct"]] = _err(actual["fulltime_hours"], f_ft)
        row[idx["hourly_hours_error_pct"]] = _err(actual["hourly_hours"], f_hourly)

        price_err = ""
        if actual["orders"] > 0 and f_orders > 0:
            actual_price = actual["net_sales"] / actual["orders"]
            forecast_price = f_net / f_orders
            price_err = _err(actual_price, forecast_price)
        row[idx["avg_order_price_error_pct"]] = price_err

        realized = ""
        if actual["net_sales"] > 0:
            realized = round(actual["total_labor_cost"] / actual["net_sales"], 4)
        row[idx["realized_labor_pct"]] = realized

        # Realized hourly (part-time-only) labor% — actual hourly_labor_cost
        # (read straight from labor_daily by name, the same cost the rest of
        # the forecast values hourly labor at) over actual net_sales. Same
        # zero-net guard as realized_labor_pct.
        total_hourly = ""
        if actual["net_sales"] > 0:
            total_hourly = round(actual["hourly_labor_cost"] / actual["net_sales"], 4)
        row[idx["total_hourly_labor_pct"]] = total_hourly

        mape_vals = []
        for a, f in [
            (actual["orders"], f_orders),
            (actual["items_sold"], f_items),
            (actual["net_sales"], f_net),
        ]:
            if a:
                mape_vals.append(abs((a - f) / a))
        row[idx["forecast_mape"]] = round(statistics.mean(mape_vals), 4) if mape_vals else ""

    return forecast_rows
