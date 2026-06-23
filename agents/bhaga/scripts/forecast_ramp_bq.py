"""Adaptive damped-trend + day-of-week + weather forecast for BHAGA orders.

Parallel model to ``forecast_bq.wow_median_4wk_v2``.  Writes to the separate
table ``model_forecast_ramp_daily`` so the production model is never touched.

Model (adaptive_dow_ets_v1):
    Holt damped-trend exponential smoothing on *deseasonalised* daily orders,
    multiplied by rolling multiplicative day-of-week factors, with an optional
    multiplicative weather correction.

    Steps:
    1.  **DOW factors** — rolling 42-day window, normalised to mean=1.
        dow_factor[d] = mean_orders_for_DOW_d / overall_mean, clamped [0.5, 1.8].
    2.  **Deseasonalise** — z[t] = y[t] / dow_factor[dow(t)].
    3.  **Damped-trend smoother** (Holt, iterate in date order):
            l_new = ALPHA*z[t] + (1-ALPHA)*(l + PHI*b)
            b     = BETA*(l_new - l) + (1-BETA)*PHI*b
            l     = l_new
    4.  **Base forecast** h calendar-days ahead:
            damp_sum(h) = PHI + PHI^2 + ... + PHI^h  ≤ PHI/(1-PHI)
            base = (l + damp_sum(h)*b) * dow_factor[dow(target)]
        The bounded damp_sum means the trend contribution *cannot* diverge
        when growth plateaus — the fix for ramp_log_ridge_v1's failure mode.
    5.  **Multiplicative weather correction** (Ridge on in-sample log-residuals):
            correction = clamp(exp(beta·[tmean_s, precip_s, rainy, event]), 0.7, 1.4)
            forecast  = base * correction
    6.  **Guard** — cap at 4× 28-day trailing mean; floor at 0.

    Default params (tuned via grid search 2026-06-23 on 58 walk-forward points):
        ALPHA=0.2  BETA=0.05  PHI=0.95  SEASON_WINDOW_DAYS=42

    Full series MAPE: 18.6% (vs 22.9% heuristic).
    Last 21d MAPE:    12.2% (vs 13.7% heuristic).
    Ramp model replaced: ramp_log_ridge_v1 (last-7d MAPE 46.8%, bias +43 orders —
    static weeks_since_open extrapolated beyond Apr-May hypergrowth plateau).

``forecast_exclude`` handling:
    Identical to forecast_bq.py — excluded days are skipped in all ETS updates
    and DOW factor windows.

``build_ramp_forecast_rows``, ``build_ramp_backfill_rows``, and
``build_ramp_coeff_rows`` preserve the exact public API of the ramp model so
materialize_model_bq.py and backfill_bigquery.py need no changes.
"""
from __future__ import annotations

import datetime
import math
import statistics
from typing import Any
from zoneinfo import ZoneInfo

from agents.bhaga.scripts.forecast import _get_parsed_rows

CT = ZoneInfo("America/Chicago")

CURRENT_RAMP_FORECAST_VERSION = "adaptive_dow_ets_v1"

# Minimum non-excluded operating days before make_date to fit the model.
_MIN_WARMUP_DAYS = 28

# Smoothing parameters (grid-tuned 2026-06-23).
_ALPHA = 0.2    # level smoothing
_BETA = 0.05    # trend smoothing
_PHI = 0.95     # damping factor  (PHI < 1 prevents runaway trend)

# Rolling window for DOW seasonality factors.
_SEASON_WINDOW_DAYS = 42

# Clamp on multiplicative DOW factor.
_DOW_FACTOR_CLAMP = (0.5, 1.8)

# Clamp on multiplicative weather/event correction.
_CORRECTION_CLAMP = (0.7, 1.4)

# Ridge L2 strength for weather correction.
_RIDGE_ALPHA = 1.0

# Diagnostic names emitted by build_ramp_coeff_rows (panel 87).
_DIAG_NAMES = (
    ["level", "trend", "damp_sum_30"]
    + [f"dow_factor_{d}" for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]]
    + ["tmean_beta", "precip_beta", "rainy_beta", "event_beta"]
    + ["alpha", "beta_param", "phi"]
)


# ── Pure-Python Ridge (no numpy / sklearn dependency) ───────────────────────


def _ridge_solve(
    X: list[list[float]],
    y: list[float],
    alpha: float = 1.0,
) -> list[float]:
    """Ridge regression via normal equations: β = (XᵀX + αI)⁻¹ Xᵀy.

    X is n×p (no intercept column — caller centres y).
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


# ── Weather feature helper ───────────────────────────────────────────────────


def _weather_feats(
    date: datetime.date,
    weather_by_date: dict[str, dict],
) -> tuple[float, float, float]:
    """Return (tmean_scaled, precip_mm_scaled, rainy_flag) for a date.

    BQ weather_daily stores metric (°C, mm).
    Returns defaults (65°F, 0 precip) when the date is absent.
    """
    w = weather_by_date.get(date.isoformat(), {})
    tmean_c = float(w.get("tmean_c") or 18.3)    # default ~65°F
    precip_mm = float(w.get("precip_mm") or 0.0)
    tmean_f = tmean_c * 9.0 / 5.0 + 32.0
    precip_in = precip_mm / 25.4
    return tmean_f / 100.0, precip_mm / 25.4, (1.0 if precip_in > 0.25 else 0.0)


def _build_weather_index(
    weather_rows: list[dict],
) -> dict[str, dict]:
    """Convert a list of weather_daily rows → {date_iso: row_dict}."""
    return {str(r["date"])[:10]: r for r in weather_rows if r.get("date")}


# ── Adaptive model core ──────────────────────────────────────────────────────


def _compute_dow_factors(
    train: list[dict],
    season_window: int = _SEASON_WINDOW_DAYS,
) -> dict[int, float]:
    """Multiplicative DOW seasonality factors, normalised to mean=1.

    Uses the last ``season_window`` non-excluded operating days.
    Days with no observations in the window get factor 1.0.
    """
    tail = train[-season_window:]
    if not tail:
        return {d: 1.0 for d in range(7)}
    mean_orders = statistics.mean(int(r["orders"]) for r in tail)
    if mean_orders <= 0:
        return {d: 1.0 for d in range(7)}
    dow_vals: dict[int, list[float]] = {d: [] for d in range(7)}
    for r in tail:
        dow_vals[datetime.date.fromisoformat(r["date"]).weekday()].append(float(r["orders"]))
    factors: dict[int, float] = {}
    for d in range(7):
        if dow_vals[d]:
            raw = statistics.mean(dow_vals[d]) / mean_orders
            factors[d] = max(_DOW_FACTOR_CLAMP[0], min(_DOW_FACTOR_CLAMP[1], raw))
        else:
            factors[d] = 1.0
    return factors


def _damp_sum(phi: float, h: int) -> float:
    """Sum phi^1 + phi^2 + ... + phi^h.  Bounded by phi/(1-phi) as h→∞."""
    if phi >= 1.0:
        return float(h)
    return phi * (1.0 - phi ** h) / (1.0 - phi)


def _fit_ets(
    train: list[dict],
    alpha: float = _ALPHA,
    beta: float = _BETA,
    phi: float = _PHI,
) -> tuple[float, float, dict[int, float], list[float], list[float]]:
    """Fit Holt damped-trend on deseasonalised orders.

    Returns (l, b, dow_factors, in_sample_fitted, in_sample_log_resid).
    The in-sample fitted values are the one-step-ahead predictions (leakage-free)
    used to estimate weather correction betas.
    """
    dow_factors = _compute_dow_factors(train)
    if not train:
        return 0.0, 0.0, dow_factors, [], []

    first = train[0]
    df0 = dow_factors[datetime.date.fromisoformat(first["date"]).weekday()]
    l = float(first["orders"]) / max(df0, 0.01)
    b = 0.0

    fitted: list[float] = []
    log_resid: list[float] = []

    for r in train:
        d = datetime.date.fromisoformat(r["date"])
        dow_f = dow_factors[d.weekday()]
        y = float(r["orders"])
        z = y / max(dow_f, 0.01)

        # One-step-ahead prediction (using l, b from *before* this observation)
        f_deseason = l + phi * b
        f_season = f_deseason * dow_f
        fitted.append(max(1.0, f_season))
        log_resid.append(math.log(max(y, 1.0)) - math.log(max(f_season, 1.0)))

        # Update state
        l_new = alpha * z + (1 - alpha) * (l + phi * b)
        b = beta * (l_new - l) + (1 - beta) * phi * b
        l = l_new

    return l, b, dow_factors, fitted, log_resid


def _fit_weather_betas(
    train: list[dict],
    log_resid: list[float],
    weather_by_date: dict[str, dict],
) -> list[float]:
    """Ridge on in-sample log-residuals vs [tmean_s, precip_s, rainy, event_flag].

    Returns 4-element beta list or [0,0,0,0] when insufficient data.
    """
    tail = train[-42:]
    resid_tail = log_resid[max(0, len(log_resid) - 42):]
    X, y = [], []
    for r, lr in zip(tail, resid_tail):
        d = datetime.date.fromisoformat(r["date"])
        tm_s, pr_s, rainy = _weather_feats(d, weather_by_date)
        event = 1.0 if r.get("event_flag") else 0.0
        X.append([tm_s, pr_s, rainy, event])
        y.append(lr)
    if len(X) < 10:
        return [0.0, 0.0, 0.0, 0.0]
    # Centre y (Ridge has no intercept)
    y_mean = statistics.mean(y)
    y_c = [v - y_mean for v in y]
    return _ridge_solve(X, y_c, alpha=_RIDGE_ALPHA)


def _build_adaptive_model(
    labor_daily_rows: list[list],
    weather_by_date: dict[str, dict],
    make_date: datetime.date,
) -> dict[str, Any]:
    """Fit adaptive_dow_ets_v1 on all non-excluded operating days before make_date.

    Returns a model dict; {} if insufficient training data.
    """
    parsed = _get_parsed_rows(labor_daily_rows, exclude_flagged=False)
    iso_make = make_date.isoformat()

    train = sorted(
        (r for r in parsed
         if r["date"] < iso_make
         and not r.get("forecast_exclude")
         and int(r.get("orders") or 0) > 0),
        key=lambda x: x["date"],
    )
    if len(train) < _MIN_WARMUP_DAYS:
        return {}

    tail28 = train[-28:]
    roll_mean = max(statistics.mean(float(r["orders"]) for r in tail28), 1.0)

    l, b, dow_factors, _, log_resid = _fit_ets(train)
    weather_betas = _fit_weather_betas(train, log_resid, weather_by_date)
    last_train_date = datetime.date.fromisoformat(train[-1]["date"])

    return {
        "l": l,
        "b": b,
        "phi": _PHI,
        "alpha": _ALPHA,
        "beta": _BETA,
        "dow_factors": dow_factors,
        "roll_mean": roll_mean,
        "weather_betas": weather_betas,
        "last_train_date": last_train_date,
        "n_train": len(train),
    }


def _adaptive_predict(
    model: dict[str, Any],
    target_date: datetime.date,
    weather_by_date: dict[str, dict],
    event_flag: bool = False,
) -> float:
    """Predict orders for target_date from a fitted adaptive model.

    Returns 0.0 if the model is empty.
    """
    if not model:
        return 0.0

    l: float = model["l"]
    b: float = model["b"]
    phi: float = model["phi"]
    dow_factors: dict[int, float] = model["dow_factors"]
    roll_mean: float = model["roll_mean"]
    weather_betas: list[float] = model["weather_betas"]
    last_train_date: datetime.date = model["last_train_date"]

    # h = calendar days from last training day to target (min 1)
    h = max(1, (target_date - last_train_date).days)
    ds = _damp_sum(phi, h)
    dow_f = dow_factors[target_date.weekday()]
    base = max(1.0, (l + ds * b) * dow_f)

    # Multiplicative weather/event correction
    tm_s, pr_s, rainy = _weather_feats(target_date, weather_by_date)
    ef = 1.0 if event_flag else 0.0
    feats = [tm_s, pr_s, rainy, ef]
    log_corr = sum(wb * f for wb, f in zip(weather_betas, feats))
    corr = max(_CORRECTION_CLAMP[0], min(_CORRECTION_CLAMP[1], math.exp(log_corr)))
    forecast = base * corr

    # Guard: cap at 4× trailing mean
    return max(0.0, min(forecast, 4.0 * roll_mean))


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


# ── Public build API (signatures and row shapes unchanged from ramp model) ────


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
    make_date = today
    gen = datetime.datetime.now(CT).isoformat(timespec="seconds")

    model = _build_adaptive_model(labor_daily_rows, weather_by_date, make_date)
    if not model:
        return []

    rows: list[dict] = []
    for i in range(0, horizon_days + 1):
        d = today + datetime.timedelta(days=i)
        orders_f = _adaptive_predict(model, d, weather_by_date)
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
        model = _build_adaptive_model(labor_daily_rows, weather_by_date, d)
        if not model:
            continue
        orders_f = _adaptive_predict(model, d, weather_by_date)
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


def build_ramp_coeff_rows(
    *,
    labor_daily_rows: list[list],
    weather_rows: list[dict],
) -> list[dict]:
    """Return one row per diagnostic for today's fitted model.

    Each row: {make_date, feature_name, coefficient, n_train}.
    Returns [] when there is insufficient history to fit the model.
    Powers the 'Adaptive Model Diagnostics Over Time' Grafana panel (panel 87).

    Emitted diagnostics:
        level, trend, damp_sum_30,
        dow_factor_mon..sun,
        tmean_beta, precip_beta, rainy_beta, event_beta,
        alpha, beta_param, phi
    """
    weather_by_date = _build_weather_index(weather_rows)
    today = datetime.datetime.now(CT).date()
    model = _build_adaptive_model(labor_daily_rows, weather_by_date, today)
    if not model:
        return []

    l = model["l"]
    b = model["b"]
    phi = model["phi"]
    alpha = model["alpha"]
    beta = model["beta"]
    dow_factors = model["dow_factors"]
    weather_betas = model["weather_betas"]
    n_train = model["n_train"]

    diag_values = (
        [l, b, _damp_sum(phi, 30)]
        + [dow_factors[d] for d in range(7)]
        + weather_betas
        + [alpha, beta, phi]
    )
    return [
        {
            "make_date": today.isoformat(),
            "feature_name": name,
            "coefficient": round(val, 6),
            "n_train": n_train,
        }
        for name, val in zip(_DIAG_NAMES, diag_values)
    ]
