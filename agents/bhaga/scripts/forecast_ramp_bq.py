"""Ramp-aware log-space Ridge forecast for BHAGA orders.

Parallel model to ``forecast_bq.wow_median_4wk_v2``.  Writes to the separate
table ``model_forecast_ramp_daily`` so the production model is never touched.

Model (ramp_log_ridge_v1):
    Fits Ridge regression on log(orders) with the following features:

    * weeks_since_open     — captures exponential ramp growth; in log-space a
                             constant weekly growth rate is a straight line.
    * DOW dummies [Mon–Sat] — Sunday is the reference (all zeros).
    * log_lag_7d, log_lag_14d, log_lag_21d, log_lag_28d
                            — log(actual_lag / rolling_mean) for each lag offset,
                              each zero-filled when the lag date is unknown at
                              forecast time (future → log(1) = 0).
    * log_roll_4w_dow      — log of the 4-week same-DOW rolling average.
    * tmean_scaled         — temperature (°F) / 100.
    * precip_mm_scaled     — precipitation (mm) / 25.4  (≈ inches).
    * rainy_flag           — 1 if precip > 6.35 mm (0.25 in), else 0.

    L2 regularisation (alpha=1.0) automatically shrinks weak features.
    Predictions are exponentiated back; a runaway-extrapolation guard caps
    the log-prediction at log(rolling_mean × 4).

``forecast_exclude`` handling:
    Identical to forecast_bq.py — excluded days are dropped from training
    (never influence weights), never used as lag look-ups.  The forward rows
    do NOT cover excluded/closed dates explicitly (the caller uses the same
    approach as the heuristic model).

Units and conversion:
    BQ weather_daily stores metric (°C, mm).  This module converts to °F/inch
    for the feature thresholds that were calibrated in the spike:
        tmean_f  = tmean_c × 9/5 + 32
        precip_in = precip_mm / 25.4
    These conversions are applied only internally; BQ schema is unchanged.

``build_ramp_forecast_rows`` and ``build_ramp_backfill_rows`` mirror the
public API of forecast_bq so materialize_model_bq.py can call them
symmetrically.
"""
from __future__ import annotations

import datetime
import math
import statistics
from typing import Any
from zoneinfo import ZoneInfo

from agents.bhaga.scripts.forecast import _get_parsed_rows

CT = ZoneInfo("America/Chicago")

CURRENT_RAMP_FORECAST_VERSION = "ramp_log_ridge_v1"

# Minimum non-excluded operating days before make_date to fit the model.
_MIN_WARMUP_DAYS = 28

# Ridge L2 regularisation strength.
_RIDGE_ALPHA = 1.0

# Lag offsets (days back from target_date).
_LAG_OFFSETS = [7, 14, 21, 28]

# Feature names in declaration order.  Used for sanity checks.
_FEAT_NAMES = (
    ["weeks_since_open"]
    + ["dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri", "dow_sat"]
    + [f"log_lag_{d}d" for d in _LAG_OFFSETS]
    + ["log_roll_4w_dow"]
    + ["tmean_scaled", "precip_mm_scaled", "rainy_flag"]
)


# ── Pure-Python Ridge (no numpy / sklearn dependency) ───────────────────────


def _ridge_solve(
    X: list[list[float]],
    y: list[float],
    alpha: float = 1.0,
) -> list[float]:
    """Ridge regression via normal equations: β = (XᵀX + αI)⁻¹ Xᵀy.

    Ported from the weather_forecast_spike run_backtest.py.
    X is n×p, y is mean-centred.  No intercept column.
    """
    n = len(X)
    p = len(X[0]) if n > 0 else 0
    if n == 0 or p == 0:
        return [0.0] * p

    XtX = [[0.0] * p for _ in range(p)]
    Xty = [0.0] * p
    for i in range(n):
        for j in range(p):
            Xty[j] += X[i][j] * y[i]
            for k in range(p):
                XtX[j][k] += X[i][j] * X[i][k]
    for j in range(p):
        XtX[j][j] += alpha

    inv = _invert_matrix(XtX)
    if inv is None:
        return [0.0] * p
    return [sum(inv[j][k] * Xty[k] for k in range(p)) for j in range(p)]


def _invert_matrix(A: list[list[float]]) -> list[list[float]] | None:
    """Invert a small square matrix via Gauss-Jordan elimination."""
    n = len(A)
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        aug[col], aug[pivot] = aug[pivot], aug[col]
        if abs(aug[col][col]) < 1e-12:
            return None
        factor = aug[col][col]
        aug[col] = [v / factor for v in aug[col]]
        for row in range(n):
            if row != col:
                mult = aug[row][col]
                aug[row] = [aug[row][k] - mult * aug[col][k] for k in range(2 * n)]
    return [row[n:] for row in aug]


# ── Weather feature helpers ──────────────────────────────────────────────────


def _weather_feats(
    date: datetime.date,
    weather_by_date: dict[str, dict],
) -> tuple[float, float, float]:
    """Return (tmean_scaled, precip_mm_scaled, rainy_flag) for a date.

    BQ weather_daily stores metric (°C, mm).  Converts to °F/inch for the
    thresholds derived from the spike.
    Returns defaults (65°F, 0 precip) when the date is absent.
    """
    w = weather_by_date.get(date.isoformat(), {})
    tmean_c = float(w.get("tmean_c") or 18.3)   # default ~65°F
    precip_mm = float(w.get("precip_mm") or 0.0)

    tmean_f = tmean_c * 9.0 / 5.0 + 32.0
    precip_in = precip_mm / 25.4

    tmean_scaled = tmean_f / 100.0
    precip_mm_scaled = precip_mm / 25.4          # ~inches
    rainy_flag = 1.0 if precip_in > 0.25 else 0.0
    return tmean_scaled, precip_mm_scaled, rainy_flag


def _build_weather_index(
    weather_rows: list[dict],
) -> dict[str, dict]:
    """Convert a list of weather_daily rows → {date_iso: row_dict}."""
    return {str(r["date"])[:10]: r for r in weather_rows if r.get("date")}


# ── Lag feature helpers ──────────────────────────────────────────────────────


def _log_lag_feats_training(
    date: datetime.date,
    by_date: dict[str, int],
    roll_mean: float,
) -> list[float]:
    """log-lag features for a training row.  All lags are in the past."""
    feats: list[float] = []
    for lag in _LAG_OFFSETS:
        iso = (date - datetime.timedelta(days=lag)).isoformat()
        v = by_date.get(iso)
        feats.append(math.log(v / roll_mean) if v and v > 0 else 0.0)

    # 4-week same-DOW rolling average
    same_dow = [
        by_date[(date - datetime.timedelta(days=7 * w)).isoformat()]
        for w in range(1, 5)
        if (date - datetime.timedelta(days=7 * w)).isoformat() in by_date
    ]
    roll = math.log(statistics.mean(same_dow) / roll_mean) if same_dow else 0.0
    feats.append(roll)
    return feats


def _log_lag_feats_prediction(
    target_date: datetime.date,
    make_date: datetime.date,
    by_date: dict[str, int],
    roll_mean: float,
) -> list[float]:
    """log-lag features for predicting target_date, leakage-free.

    Lag dates on or after make_date are unknown → 0.0 (= log(1)).
    """
    iso_make = make_date.isoformat()
    feats: list[float] = []
    for lag in _LAG_OFFSETS:
        iso = (target_date - datetime.timedelta(days=lag)).isoformat()
        if iso >= iso_make:
            feats.append(0.0)
        else:
            v = by_date.get(iso)
            feats.append(math.log(v / roll_mean) if v and v > 0 else 0.0)

    same_dow = [
        by_date[(target_date - datetime.timedelta(days=7 * w)).isoformat()]
        for w in range(1, 5)
        if (target_date - datetime.timedelta(days=7 * w)).isoformat() in by_date
        and (target_date - datetime.timedelta(days=7 * w)).isoformat() < iso_make
    ]
    roll = math.log(statistics.mean(same_dow) / roll_mean) if same_dow else 0.0
    feats.append(roll)
    return feats


# ── Core model fit / predict ─────────────────────────────────────────────────


def _build_ramp_model(
    labor_daily_rows: list[list],
    weather_by_date: dict[str, dict],
    make_date: datetime.date,
) -> dict[str, Any]:
    """Fit log-space Ridge on all non-excluded operating days before make_date.

    Returns a model dict; {} if insufficient training data.
    """
    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=False)
    iso_make = make_date.isoformat()

    # Training: non-excluded days strictly before make_date with orders > 0.
    train = sorted(
        (r for r in parsed
         if r["date"] < iso_make
         and not r.get("forecast_exclude")
         and int(r.get("orders") or 0) > 0),
        key=lambda x: x["date"],
    )
    if len(train) < _MIN_WARMUP_DAYS:
        return {}

    first_date = datetime.date.fromisoformat(train[0]["date"])
    by_date: dict[str, int] = {r["date"]: int(r["orders"]) for r in train}
    tail = train[-28:]
    roll_mean = max(statistics.mean(int(r["orders"]) for r in tail), 1.0)

    X: list[list[float]] = []
    y: list[float] = []
    for r in train:
        d = datetime.date.fromisoformat(r["date"])
        weeks_since = (d - first_date).days / 7.0
        dow = d.weekday()
        dummies = [1.0 if dow == i else 0.0 for i in range(6)]
        log_lags = _log_lag_feats_training(d, by_date, roll_mean)
        tm_s, pr_s, rainy = _weather_feats(d, weather_by_date)
        feats = [weeks_since] + dummies + log_lags + [tm_s, pr_s, rainy]
        X.append(feats)
        y.append(math.log(int(r["orders"])))

    y_mean = statistics.mean(y)
    y_c = [v - y_mean for v in y]
    beta = _ridge_solve(X, y_c, alpha=_RIDGE_ALPHA)
    return {
        "beta": beta,
        "intercept": y_mean,
        "first_date": first_date,
        "roll_mean": roll_mean,
        "by_date": by_date,
        "n_train": len(train),
        "feat_names": _FEAT_NAMES,
    }


def _ramp_predict(
    model: dict[str, Any],
    target_date: datetime.date,
    make_date: datetime.date,
    weather_by_date: dict[str, dict],
) -> float:
    """Predict orders for target_date from a fitted ramp model.

    Returns 0.0 if the model is empty.
    """
    if not model:
        return 0.0

    first_date: datetime.date = model["first_date"]
    beta: list[float] = model["beta"]
    y_mean: float = model["intercept"]
    roll_mean: float = model["roll_mean"]
    by_date: dict[str, int] = model["by_date"]

    weeks_since = (target_date - first_date).days / 7.0
    dow = target_date.weekday()
    dummies = [1.0 if dow == i else 0.0 for i in range(6)]
    log_lags = _log_lag_feats_prediction(target_date, make_date, by_date, roll_mean)
    tm_s, pr_s, rainy = _weather_feats(target_date, weather_by_date)
    feats = [weeks_since] + dummies + log_lags + [tm_s, pr_s, rainy]

    if len(feats) != len(beta):
        return 0.0

    log_pred = y_mean + sum(b * x for b, x in zip(beta, feats))
    # Guard: cap at 4× the trailing mean to prevent runaway extrapolation.
    log_pred = min(log_pred, math.log(roll_mean * 4))
    return max(0.0, math.exp(log_pred))


def _items_from_orders(
    orders_pred: float,
    labor_daily_rows: list[list],
    make_date: datetime.date,
) -> float:
    """Estimate forecast_items from a recent items-per-order ratio.

    Falls back to 1.5 items/order (Palmetto typical) when history is thin.
    """
    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=False)
    iso_make = make_date.isoformat()
    recent = sorted(
        (r for r in parsed
         if r["date"] < iso_make
         and not r.get("forecast_exclude")
         and int(r.get("orders") or 0) > 0
         and float(r.get("items_sold") or 0) > 0),
        key=lambda x: x["date"],
    )[-28:]
    if not recent:
        ratio = 1.5
    else:
        total_items = sum(float(r.get("items_sold") or 0) for r in recent)
        total_orders = sum(int(r.get("orders") or 0) for r in recent)
        ratio = total_items / total_orders if total_orders > 0 else 1.5
    return round(orders_pred * ratio, 1)


# ── Public build API ─────────────────────────────────────────────────────────


def build_ramp_forecast_rows(
    *,
    labor_daily_rows: list[list],
    weather_rows: list[dict],
    horizon_days: int = 30,
) -> list[dict]:
    """Return forecast rows for today … today+horizon_days (Chicago time).

    Each row: {date, forecast_orders, forecast_items, forecast_generated_at,
               forecast_model_version}.
    Returns [] when there is insufficient history to fit the model.
    """
    weather_by_date = _build_weather_index(weather_rows)
    today = datetime.datetime.now(CT).date()
    make_date = today  # weights fit on data strictly before today
    gen = datetime.datetime.now(CT).isoformat(timespec="seconds")

    model = _build_ramp_model(labor_daily_rows, weather_by_date, make_date)
    if not model:
        return []

    rows: list[dict] = []
    for i in range(0, horizon_days + 1):
        d = today + datetime.timedelta(days=i)
        orders_f = _ramp_predict(model, d, make_date, weather_by_date)
        if orders_f <= 0:
            continue
        rows.append({
            "date": d.isoformat(),
            "forecast_orders": max(0, round(orders_f)),
            "forecast_items": _items_from_orders(orders_f, labor_daily_rows, make_date),
            "forecast_generated_at": gen,
            "forecast_model_version": CURRENT_RAMP_FORECAST_VERSION,
        })
    return rows


def build_ramp_backfill_rows(
    *,
    labor_daily_rows: list[list],
    weather_rows: list[dict],
    weeks: int = 8,
) -> list[dict]:
    """Leakage-free forecasts for PAST dates (gap-fill for forecast accuracy view).

    For each non-excluded operating day D in the last ``weeks`` weeks (before
    today), refit the model using only data strictly before D.  These rows feed
    vw_forecast_ramp_accuracy without appearing in the forward table.
    The caller drops rows whose date already exists in model_forecast_ramp_daily.
    """
    weather_by_date = _build_weather_index(weather_rows)
    today = datetime.datetime.now(CT).date()
    horizon_start = today - datetime.timedelta(days=7 * weeks)
    gen = datetime.datetime.now(CT).isoformat(timespec="seconds")

    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=False)
    operating_dates = sorted(
        r["date"] for r in parsed
        if not r.get("forecast_exclude")
        and int(r.get("orders") or 0) > 0
    )

    rows: list[dict] = []
    for d_iso in operating_dates:
        d = datetime.date.fromisoformat(d_iso)
        if d < horizon_start or d >= today:
            continue
        model = _build_ramp_model(labor_daily_rows, weather_by_date, d)
        if not model:
            continue
        orders_f = _ramp_predict(model, d, d, weather_by_date)
        if orders_f <= 0:
            continue
        rows.append({
            "date": d_iso,
            "forecast_orders": max(0, round(orders_f)),
            "forecast_items": _items_from_orders(orders_f, labor_daily_rows, d),
            "forecast_generated_at": gen,
            "forecast_model_version": CURRENT_RAMP_FORECAST_VERSION,
        })
    return rows
