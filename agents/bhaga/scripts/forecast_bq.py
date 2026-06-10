"""Compute the slim BQ forecast (orders + items) for the next N days.

BQ-authoritative replacement for the retired labor_daily_forecast Sheet tab.

Model (wow_median_4wk_v2, 2026-06-10):
  forecast(day) = anchor × growth ** weeks_apart

  * anchor  = the most recent ACTUAL same-weekday operating day strictly before
              the forecast cutoff. We walk back 7, 14, 21 … days (whole weeks, so
              the day-of-week always matches) and take the first day that is an
              operating day (orders > 0) AND not flagged forecast_exclude. This is
              the "smarter fallback": an excluded/closed last week simply skips to
              the week before, never to a different weekday.
  * growth  = median of consecutive same-weekday week-over-week order ratios over
              the past 4 weeks (_GROWTH_WINDOW_DAYS = 28). For each usable day d
              in that window whose same-weekday predecessor d-7 is also usable,
              ratio = orders(d)/orders(d-7). growth = median(ratios), clamped to
              [_GROWTH_MIN, _GROWTH_MAX]. Pooling ~19 ratios + taking the median
              keeps it a true weekly step and is robust to one anomalous week
              (e.g. the median ignores Memorial-recovery 2.3x spikes). Returns
              1.0 when fewer than 2 valid pairs exist.
  * weeks_apart = how many whole weeks the anchor sits before the forecast day,
              so growth compounds once per week (next week ×g, the week after ×g²).

Items use the SAME anchor day's actual items × growth — not a derived ratio — so
a day's item mix carries straight through.

The nightly loader writes today+1 … today+horizon with merge_keys=["date"], so
future rows update with each run and freeze once the day passes. Past rows are
written only when they don't already exist (gap-fill-only), so history is stable
across model updates. Each row is stamped with CURRENT_FORECAST_VERSION so
vw_forecast_accuracy can track which model predicted each day.

``build_backfill_rows`` writes leakage-free forecasts for PAST dates (each
computed using only data strictly before it) so the accuracy view has history the
moment the feature ships. The caller (materialize_model_bq) drops any b_rows
whose date already exists in model_forecast_daily before loading.
"""
from __future__ import annotations

import datetime
import statistics
from zoneinfo import ZoneInfo

from agents.bhaga.scripts.forecast import _get_parsed_rows

CT = ZoneInfo("America/Chicago")

# Growth multiplier guard rails — a runaway week-over-week ratio (holiday week,
# a near-closed week) must not compound into an absurd 30-day projection.
_GROWTH_MIN = 0.80
_GROWTH_MAX = 1.20
# Window for the consecutive same-weekday WoW ratio pool (4 weeks ≈ 19 ratios
# when history is clean; median over this pool is robust to one anomalous week).
_GROWTH_WINDOW_DAYS = 28
# How many whole weeks back we'll hunt for a usable same-weekday anchor before
# giving up and falling back to a recent-days average.
_MAX_ANCHOR_WEEKS = 8

# Version tag stamped on every forecast row so vw_forecast_accuracy can identify
# which model produced a past prediction. Bump this when the growth logic changes.
# The strategy registry maps version -> growth function so old versions are
# retained (future: pass version explicitly to build a historical comparison).
CURRENT_FORECAST_VERSION = "wow_median_4wk_v2"
_GROWTH_STRATEGIES: dict[str, object] = {}  # populated after _growth_multiplier is defined below


def _index_operating_days(labor_daily_rows: list[list]) -> dict[str, dict]:
    """Map ISO date → {orders, items, dow, excluded} for every operating day.

    Includes forecast_exclude'd days (we need to KNOW a day was excluded so the
    anchor walk-back can skip it) but drops closed/zero-order days.
    """
    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=False)
    out: dict[str, dict] = {}
    for r in parsed:
        out[r["date"]] = {
            "orders": int(r["orders"]),
            "items": float(r.get("items_sold") or 0),
            "dow": r["dow"],
            "excluded": bool(r.get("forecast_exclude")),
        }
    return out


def _growth_multiplier(by_date: dict[str, dict], cutoff: datetime.date) -> float:
    """Median of consecutive same-weekday week-over-week order ratios, last 4 weeks.

    For each usable operating day d with cutoff-28 <= d < cutoff whose same-weekday
    predecessor d-7 is also usable, ratio = orders(d)/orders(d-7). Returns
    median(ratios), clamped to [_GROWTH_MIN, _GROWTH_MAX]; 1.0 when <2 ratios.

    Usable = non-excluded AND orders > 0. Each ratio is a true 1-week step;
    pooling 4 weeks of such ratios (~19 when history is clean) + taking the
    median makes the estimate robust to one anomalous week (a single 2.4x ratio
    from a Memorial-week comparison moves the median by little when there are
    18 other well-behaved ratios). Leakage-free: only days strictly before cutoff.
    """
    iso = cutoff.isoformat()
    start = (cutoff - datetime.timedelta(days=_GROWTH_WINDOW_DAYS)).isoformat()
    usable = {
        d: rec for d, rec in by_date.items()
        if d < iso and not rec["excluded"] and rec["orders"] > 0
    }
    ratios: list[float] = []
    for d_iso, rec in usable.items():
        if d_iso < start:
            continue
        prev_iso = (datetime.date.fromisoformat(d_iso) - datetime.timedelta(days=7)).isoformat()
        prev = usable.get(prev_iso)
        if prev:
            ratios.append(rec["orders"] / prev["orders"])
    if len(ratios) < 2:
        return 1.0
    return max(_GROWTH_MIN, min(_GROWTH_MAX, statistics.median(ratios)))


def _forecast_one(
    target: datetime.date,
    by_date: dict[str, dict],
    cutoff: datetime.date,
    growth: float,
) -> dict | None:
    """Forecast a single day: same-weekday anchor × growth**weeks_apart.

    ``cutoff`` is exclusive — only actual days strictly before it may be used as
    an anchor (so a historical backfill never peeks at the day it's forecasting).
    Returns None when there is no usable history at all.
    """
    for weeks in range(1, _MAX_ANCHOR_WEEKS + 1):
        cand = target - datetime.timedelta(days=7 * weeks)
        if cand >= cutoff:
            continue  # not an actual yet
        rec = by_date.get(cand.isoformat())
        if rec and rec["orders"] > 0 and not rec["excluded"]:
            factor = growth ** weeks
            return {
                "orders": max(0, round(rec["orders"] * factor)),
                "items": max(0.0, round(rec["items"] * factor, 1)),
            }
    # Fallback: no same-weekday anchor in the last _MAX_ANCHOR_WEEKS weeks — use
    # the mean of the most recent (up to) 7 usable operating days × growth.
    iso = cutoff.isoformat()
    recent = sorted(
        ((d, rec) for d, rec in by_date.items()
         if d < iso and d < target.isoformat() and not rec["excluded"]),
        key=lambda kv: kv[0], reverse=True,
    )[:7]
    if not recent:
        return None
    avg_orders = statistics.mean(rec["orders"] for _, rec in recent)
    avg_items = statistics.mean(rec["items"] for _, rec in recent)
    return {
        "orders": max(0, round(avg_orders * growth)),
        "items": max(0.0, round(avg_items * growth, 1)),
    }


def build_forecast_rows(
    *,
    labor_daily_rows: list[list],
    wage_rates: list[dict] | None = None,  # unused since 2026-06-10; kept for caller compat
    horizon_days: int = 30,
) -> list[dict]:
    """Return [{date, forecast_orders, forecast_items, forecast_generated_at,
    forecast_model_version}] for today+1 … today+horizon_days (Chicago time).

    Each day = the most recent same-weekday actual × the growth multiplier,
    compounded by the number of weeks between anchor and forecast day. Excluded /
    closed anchor days are skipped a whole week at a time (DOW preserved).
    Returns [] when there is no operating-day history to anchor on.
    """
    by_date = _index_operating_days(labor_daily_rows)
    if not by_date:
        return []

    today = datetime.datetime.now(CT).date()
    gen = datetime.datetime.now(CT).isoformat(timespec="seconds")
    growth = _growth_multiplier(by_date, cutoff=today)

    rows: list[dict] = []
    for i in range(1, horizon_days + 1):
        d = today + datetime.timedelta(days=i)
        fc = _forecast_one(d, by_date, cutoff=today, growth=growth)
        if fc is None:
            continue
        rows.append({
            "date": d.isoformat(),
            "forecast_orders": fc["orders"],
            "forecast_items": fc["items"],
            "forecast_generated_at": gen,
            "forecast_model_version": CURRENT_FORECAST_VERSION,
        })
    return rows


def build_backfill_rows(
    *,
    labor_daily_rows: list[list],
    weeks: int = 8,
) -> list[dict]:
    """Leakage-free forecasts for PAST dates, so forecast-vs-actual has history.

    For each operating day D in the last ``weeks`` weeks (excluding today), compute
    what the forecaster WOULD have produced one day ahead using only actuals
    strictly before D (cutoff=D). These rows are written to model_forecast_daily
    with date < today, so they feed vw_forecast_accuracy (same-date actual join)
    without appearing in the forward vw_model_forecast (which filters date>=today).

    The caller (materialize_model_bq) drops any row whose date already exists in
    model_forecast_daily before loading (gap-fill-only), so history is stable
    across model updates.
    """
    by_date = _index_operating_days(labor_daily_rows)
    if not by_date:
        return []

    today = datetime.datetime.now(CT).date()
    horizon_start = today - datetime.timedelta(days=7 * weeks)
    gen = datetime.datetime.now(CT).isoformat(timespec="seconds")

    rows: list[dict] = []
    for d_iso in sorted(by_date):
        d = datetime.date.fromisoformat(d_iso)
        if d < horizon_start or d >= today:
            continue
        growth = _growth_multiplier(by_date, cutoff=d)
        fc = _forecast_one(d, by_date, cutoff=d, growth=growth)
        if fc is None:
            continue
        rows.append({
            "date": d.isoformat(),
            "forecast_orders": fc["orders"],
            "forecast_items": fc["items"],
            "forecast_generated_at": gen,
            "forecast_model_version": CURRENT_FORECAST_VERSION,
        })
    return rows


# Populate strategy registry after the function is defined.
_GROWTH_STRATEGIES[CURRENT_FORECAST_VERSION] = _growth_multiplier
