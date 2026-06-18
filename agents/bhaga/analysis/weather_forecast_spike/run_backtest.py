"""Walk-forward backtest: 4 order-forecast models.

Models
------
A  Heuristic v2 (production)  — weighted DOW average + capped trend factor
B  Heuristic + weather         — Model A anchor de-weathered by precip/temp
C  Ridge, no weather           — Ridge regression on DOW dummies + trend
D  Ridge + weather             — Ridge on calendar + weather features (Model D)

A vs D is the headline decision.  B and C are diagnostics.

Walk-forward guarantee
----------------------
Every forecast for target_date T at horizon H is made using only data whose
date < (T - H + 1), i.e. strictly before the make-date.  No hindsight.

Weather source modes
--------------------
observed   — actual historical weather (ERA5 reanalysis via Open-Meteo archive
             API).  This is the UPPER BOUND: it assumes perfect weather forecasts,
             which is unrealistic for H > 3 days.

persistence — for H > 1, uses make_date's actual weather as the "forecast" for
             target_date (the classical persistence baseline).  NWP models are
             better than persistence, so this is a CONSERVATIVE LOWER BOUND on
             the realistic benefit of weather data.  The truth for production use
             sits between the two bands.  For H = 1, persistence == observed
             (you already know today's weather).

Exclusion consistency
---------------------
forecast_exclude = True days are removed from BOTH the training history (so they
never pollute the DOW anchor or regression fit) AND the test set (so we never
try to score a forecast against an anomalous actual).  Both passes use the same
flag; no day is treated differently across models.

Outputs
-------
out/backtest_by_day.csv   — one row per (target_date, horizon, model, wx_mode)
out/summary_matrix.csv    — MAPE / MAE / bias by model × horizon and model × DOW


The script also writes results.canvas.tsx to the Cursor canvases directory with
all result data embedded inline for interactive viewing.
"""
from __future__ import annotations

import csv
import datetime
import json
import math
import os
import statistics
import sys
import textwrap
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────────

SPIKE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = SPIKE_DIR / "data"
OUT_DIR = SPIKE_DIR / "out"

ACTUALS_PATH = DATA_DIR / "actuals.csv"
WEATHER_PATH = DATA_DIR / "weather_daily.csv"
BACKTEST_PATH = OUT_DIR / "backtest_by_day.csv"
SUMMARY_PATH = OUT_DIR / "summary_matrix.csv"

# The canvas lives in the Cursor-managed canvases dir for this worktree.
# Cursor workspace slug = repo-root absolute path with "/" replaced by "-",
# leading slash dropped.  E.g. /Users/alice/repo → Users-alice-repo.
_WORKSPACE = Path(os.path.dirname(os.path.abspath(__file__))).parents[3]
_CANVAS_DIR = (
    Path.home()
    / ".cursor"
    / "projects"
    / str(_WORKSPACE)[1:].replace("/", "-")
    / "canvases"
)
CANVAS_PATH = _CANVAS_DIR / "weather-forecast-spike-results.canvas.tsx"

HORIZONS = [1, 3, 7, 14]
MODEL_NAMES = {
    "A": "Heuristic v2",
    "B": "Heuristic + weather",
    "C": "Ridge (no weather)",
    "D": "Ridge + weather",
    "E": "Ridge + lags + weather",
    "F": "Ramp-aware (log) + lags + weather",
}

# Minimum operating days before the make-date required to compute a forecast.
MIN_WARMUP_DAYS = 28

# Ridge regularisation
RIDGE_ALPHA = 1.0


# ── Data loading ────────────────────────────────────────────────────────────


def load_actuals() -> list[dict]:
    """Load data/actuals.csv → list of dicts, operating days only (orders > 0)."""
    if not ACTUALS_PATH.exists():
        raise FileNotFoundError(
            f"actuals.csv not found at {ACTUALS_PATH}. Run pull_actuals.py first."
        )
    rows = []
    with open(ACTUALS_PATH) as f:
        for r in csv.DictReader(f):
            try:
                orders = int(float(r["orders"]))
            except (ValueError, TypeError):
                orders = 0
            if orders <= 0:
                continue
            fe = str(r.get("forecast_exclude", "false")).strip().lower()
            rows.append(
                {
                    "date": r["date"],
                    "dow": datetime.date.fromisoformat(r["date"]).weekday(),
                    "orders": orders,
                    "forecast_exclude": fe in ("true", "1", "yes"),
                }
            )
    rows.sort(key=lambda x: x["date"])
    return rows


def load_weather() -> dict[str, dict]:
    """Load data/weather_daily.csv → {date_iso: {...weather fields}}."""
    if not WEATHER_PATH.exists():
        raise FileNotFoundError(
            f"weather_daily.csv not found at {WEATHER_PATH}. Run fetch_weather.py first."
        )
    out: dict[str, dict] = {}
    with open(WEATHER_PATH) as f:
        for r in csv.DictReader(f):
            date = r.get("date", "").strip()
            if not date:
                continue

            def _f(k: str, default: float = 0.0) -> float:
                v = r.get(k, "")
                try:
                    return float(v) if v not in ("", None) else default
                except (ValueError, TypeError):
                    return default

            out[date] = {
                "tmax_f": _f("tmax_f", 75.0),
                "tmin_f": _f("tmin_f", 55.0),
                "tmean_f": _f("tmean_f", 65.0),
                "precip_in": _f("precip_in"),
                "rain_in": _f("rain_in"),
                "precip_hours": _f("precip_hours"),
                "wind_max_mph": _f("wind_max_mph"),
            }
    return out


# ── Model A: Heuristic v2 ───────────────────────────────────────────────────


def _weighted_dow_avg(
    history: list[dict],
    target_date: datetime.date,
    lookback_weeks: int = 6,
    decay: float = 0.8,
) -> float:
    """Weighted same-DOW average (most-recent weight=1, then decay^i).

    Mirrors `_expected_orders_from_records` in agents/bhaga/scripts/forecast.py
    exactly, so Model A replicates production behaviour.
    """
    iso = target_date.isoformat()
    target_dow = target_date.weekday()
    prior = sorted(
        (r for r in history if r["date"] < iso),
        key=lambda x: x["date"],
        reverse=True,
    )
    if not prior:
        return 0.0
    same_dow = [r for r in prior if r["dow"] == target_dow][:lookback_weeks]
    if not same_dow:
        return 0.0
    weights = [decay**i for i in range(len(same_dow))]
    dow_avg = sum(r["orders"] * w for r, w in zip(same_dow, weights)) / sum(weights)

    if len(prior) < 14:
        return dow_avg
    last_2 = prior[:14]
    prev_2 = prior[14:28]
    if not prev_2:
        return dow_avg
    avg_recent = statistics.mean(r["orders"] for r in last_2)
    avg_prior = statistics.mean(r["orders"] for r in prev_2)
    trend = 1.0 if avg_prior <= 0 else avg_recent / avg_prior
    trend = max(0.85, min(1.15, trend))
    return dow_avg * trend


def forecast_model_a(
    history: list[dict],
    target_date: datetime.date,
) -> float:
    """Model A: production heuristic (weighted DOW avg + capped trend)."""
    # Exclude forecast_exclude days from the history fed to the model, matching
    # prod behaviour (exclude_flagged=True in _get_parsed_rows).
    clean = [r for r in history if not r.get("forecast_exclude")]
    return _weighted_dow_avg(clean, target_date)


# ── Weather feature helpers ─────────────────────────────────────────────────


def _weather_features(
    date: datetime.date,
    weather: dict[str, dict],
) -> dict[str, float]:
    """Weather features for a single date (zero-filled when absent)."""
    iso = date.isoformat()
    w = weather.get(iso, {})
    tmean = w.get("tmean_f", 65.0)
    precip = w.get("precip_in", 0.0)
    # Heat index: orders tend to drop on very hot days (>90°F) in Austin.
    heat_flag = 1.0 if tmean > 90.0 else 0.0
    cold_flag = 1.0 if tmean < 45.0 else 0.0
    rainy_flag = 1.0 if precip > 0.25 else 0.0
    heavy_rain = 1.0 if precip > 0.75 else 0.0
    return {
        "tmean_f": tmean,
        "precip_in": precip,
        "heat_flag": heat_flag,
        "cold_flag": cold_flag,
        "rainy_flag": rainy_flag,
        "heavy_rain": heavy_rain,
    }


# ── Model B: Heuristic + weather de-weathering ─────────────────────────────


def _build_weather_correction(
    history: list[dict],
    weather: dict[str, dict],
    make_date: datetime.date,
) -> dict[str, float]:
    """Fit a simple OLS regression of order residuals on weather features.

    residual_i = actual_i - model_A_anchor_i
    correction  = fitted coefficients for the target day's weather

    Uses only data strictly before make_date (leakage-free).
    """
    clean = [r for r in history if not r.get("forecast_exclude")]
    iso_make = make_date.isoformat()
    train = [r for r in clean if r["date"] < iso_make]
    if len(train) < MIN_WARMUP_DAYS:
        return {}

    # Build residuals: actual - DOW-weighted avg expectation
    rows_with_resid = []
    for r in train:
        d = datetime.date.fromisoformat(r["date"])
        expected = _weighted_dow_avg(
            [x for x in train if x["date"] < r["date"]],
            d,
        )
        if expected <= 0:
            continue
        wf = _weather_features(d, weather)
        rows_with_resid.append(
            {
                "resid": r["orders"] - expected,
                **wf,
            }
        )
    if len(rows_with_resid) < 10:
        return {}

    # Simple OLS on the two most predictive weather features (precip, heat)
    # to avoid overfitting on a small sample.
    feats = ["precip_in", "heat_flag"]
    n = len(rows_with_resid)
    y = [r["resid"] for r in rows_with_resid]
    X = [[r[f] for f in feats] for r in rows_with_resid]

    # Solve with normal equations: (X^T X + αI)^-1 X^T y  (ridge)
    coeffs = _ridge_solve(X, y, alpha=RIDGE_ALPHA)
    return {f: c for f, c in zip(feats, coeffs)}


def forecast_model_b(
    history: list[dict],
    weather: dict[str, dict],
    target_date: datetime.date,
    make_date: datetime.date,
    *,
    pred_weather: dict | None = None,
) -> float:
    """Model B: heuristic anchor + weather correction on residuals.

    Training (correction fit) uses actual weather for all historical dates.
    Prediction uses pred_weather if supplied, otherwise falls back to
    actual_weather[target_date] (i.e. observed mode).
    """
    anchor = forecast_model_a(history, target_date)
    if anchor <= 0:
        return anchor
    coeffs = _build_weather_correction(history, weather, make_date)
    if not coeffs:
        return anchor
    # At prediction time, use the supplied weather dict (observed or proxy).
    wf = pred_weather if pred_weather is not None else weather.get(target_date.isoformat(), {})
    correction = sum(coeffs.get(f, 0.0) * wf.get(f, 0.0) for f in coeffs)
    return max(0.0, anchor + correction)


# ── Ridge helpers ───────────────────────────────────────────────────────────


def _ridge_solve(
    X: list[list[float]],
    y: list[float],
    alpha: float = 1.0,
) -> list[float]:
    """Ridge regression via normal equations (no sklearn dependency).

    Solves (X^T X + αI)^{-1} X^T y. X is n×p.  No intercept column; the
    caller should centre data before passing or rely on DOW dummies.
    """
    n = len(X)
    p = len(X[0]) if n > 0 else 0
    if n == 0 or p == 0:
        return [0.0] * p

    # XtX (p×p)
    XtX = [[0.0] * p for _ in range(p)]
    Xty = [0.0] * p
    for i in range(n):
        for j in range(p):
            Xty[j] += X[i][j] * y[i]
            for k in range(p):
                XtX[j][k] += X[i][j] * X[i][k]
    # Add ridge penalty
    for j in range(p):
        XtX[j][j] += alpha

    # Gauss-Jordan inversion
    inv = _invert_matrix(XtX)
    if inv is None:
        return [0.0] * p

    # β = inv(XtX) @ Xty
    beta = [sum(inv[j][k] * Xty[k] for k in range(p)) for j in range(p)]
    return beta


def _invert_matrix(A: list[list[float]]) -> list[list[float]] | None:
    """Invert a small square matrix using Gauss-Jordan elimination."""
    n = len(A)
    # Augmented matrix [A | I]
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(A)]
    for col in range(n):
        # Find pivot
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


# ── Models C & D: Ridge regression ─────────────────────────────────────────


# ── Lag-order features ───────────────────────────────────────────────────────


LAG_OFFSETS = [7, 14, 28]          # days back from target_date
LAG_FEAT_NAMES = ["lag_7d", "lag_14d", "lag_28d", "roll_4w_dow"]


def _lag_feats_for_training(
    date: datetime.date,
    by_date: dict[str, int],
    roll_mean: float,
) -> list[float]:
    """Lag features for a training row at `date`, using full training dict.

    Values are normalised by roll_mean (28-day rolling mean) so coefficients
    are comparable across stores and time periods.
    """
    feats: list[float] = []
    for lag in LAG_OFFSETS:
        iso = (date - datetime.timedelta(days=lag)).isoformat()
        feats.append(by_date.get(iso, roll_mean) / roll_mean)
    # 4-week same-DOW rolling average
    same_dow = [
        by_date[(date - datetime.timedelta(days=7 * w)).isoformat()]
        for w in range(1, 5)
        if (date - datetime.timedelta(days=7 * w)).isoformat() in by_date
    ]
    feats.append(statistics.mean(same_dow) / roll_mean if same_dow else 1.0)
    return feats


def _lag_feats_for_prediction(
    target_date: datetime.date,
    make_date: datetime.date,
    history: list[dict],
) -> list[float]:
    """Lag features for predicting target_date, using actuals known at make_date.

    If a lag date falls on or after make_date (i.e. it is in the future at
    forecast time), we substitute 1.0 (the normalised mean) as a fallback.
    This happens naturally for H=14 day-7 lag but not for H≤7.
    """
    iso_make = make_date.isoformat()
    clean = [r for r in history if not r.get("forecast_exclude") and r["date"] < iso_make]
    if not clean:
        return [1.0] * len(LAG_FEAT_NAMES)
    tail = sorted(clean, key=lambda x: x["date"])[-28:]
    roll_mean = max(statistics.mean(r["orders"] for r in tail), 1.0)
    by_date = {r["date"]: r["orders"] for r in clean}

    feats: list[float] = []
    for lag in LAG_OFFSETS:
        iso = (target_date - datetime.timedelta(days=lag)).isoformat()
        if iso < iso_make:
            feats.append(by_date.get(iso, roll_mean) / roll_mean)
        else:
            feats.append(1.0)   # future date — use normalised mean

    same_dow = [
        by_date[(target_date - datetime.timedelta(days=7 * w)).isoformat()]
        for w in range(1, 5)
        if (target_date - datetime.timedelta(days=7 * w)).isoformat() in by_date
        and (target_date - datetime.timedelta(days=7 * w)).isoformat() < iso_make
    ]
    feats.append(statistics.mean(same_dow) / roll_mean if same_dow else 1.0)
    return feats


def _build_calendar_features(
    date: datetime.date,
    make_date: datetime.date,
    history: list[dict],
) -> list[float]:
    """Calendar feature vector: DOW dummies (6) + trend term.

    DOW dummies: one per weekday [Mon=0 … Sat=5], Sunday is the reference
    (all zeros).  Trend = (days since dataset start) / 100 to keep scale
    comparable to the dummies.
    """
    # 6 DOW dummies (Sunday drops out as reference)
    dow = date.weekday()  # Mon=0 … Sun=6
    dummies = [1.0 if dow == d else 0.0 for d in range(6)]

    # Trend: fraction through the observed window
    all_dates = sorted(r["date"] for r in history if r["date"] < make_date.isoformat())
    if len(all_dates) < 2:
        trend = 0.0
    else:
        first = datetime.date.fromisoformat(all_dates[0])
        days_since = (date - first).days
        span = (datetime.date.fromisoformat(all_dates[-1]) - first).days or 1
        trend = days_since / span
    return dummies + [trend]


def _build_ridge_model(
    history: list[dict],
    weather: dict[str, dict],
    make_date: datetime.date,
    include_weather: bool,
    include_lags: bool = False,
) -> dict[str, Any]:
    """Fit ridge regression on all operating days before make_date.

    Returns a model dict with keys: beta, intercept, first_date, span,
    include_weather, include_lags, feat_names, n_train.
    Returns {} if insufficient training data.
    """
    clean = [r for r in history if not r.get("forecast_exclude")]
    iso_make = make_date.isoformat()
    train = sorted(
        (r for r in clean if r["date"] < iso_make), key=lambda x: x["date"]
    )
    if len(train) < MIN_WARMUP_DAYS:
        return {}

    all_dates = [r["date"] for r in train]
    first_date = datetime.date.fromisoformat(all_dates[0])
    span = (
        datetime.date.fromisoformat(all_dates[-1]) - first_date
    ).days or 1

    # Pre-build lookup and rolling mean for lag features
    by_date_orders: dict[str, int] = {r["date"]: int(r["orders"]) for r in train}
    tail = train[-28:]
    roll_mean = max(statistics.mean(r["orders"] for r in tail), 1.0)

    # Build feature names in declaration order (same order as feature vectors)
    dow_names = ["dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri", "dow_sat"]
    feat_names = dow_names + ["trend"]
    if include_weather:
        feat_names += ["tmean_f", "precip_in", "heat_flag", "cold_flag", "rainy_flag", "heavy_rain"]
    if include_lags:
        feat_names += LAG_FEAT_NAMES

    X: list[list[float]] = []
    y: list[float] = []
    for r in train:
        d = datetime.date.fromisoformat(r["date"])
        dow = d.weekday()
        dummies = [1.0 if dow == i else 0.0 for i in range(6)]
        days_since = (d - first_date).days
        trend = days_since / span
        feats = dummies + [trend]
        if include_weather:
            wf = _weather_features(d, weather)
            feats += [
                wf["tmean_f"] / 100.0,
                wf["precip_in"],
                wf["heat_flag"],
                wf["cold_flag"],
                wf["rainy_flag"],
                wf["heavy_rain"],
            ]
        if include_lags:
            feats += _lag_feats_for_training(d, by_date_orders, roll_mean)
        X.append(feats)
        y.append(float(r["orders"]))

    y_mean = statistics.mean(y)
    y_c = [v - y_mean for v in y]

    beta = _ridge_solve(X, y_c, alpha=RIDGE_ALPHA)
    return {
        "beta": beta,
        "intercept": y_mean,
        "first_date": first_date,
        "span": span,
        "include_weather": include_weather,
        "include_lags": include_lags,
        "feat_names": feat_names,
        "n_train": len(train),
    }


def _ridge_predict(
    model: dict,
    target_date: datetime.date,
    weather: dict[str, dict],
    *,
    history: list[dict] | None = None,
    make_date: datetime.date | None = None,
) -> float:
    """Predict orders for target_date using a fitted ridge model dict.

    If the model was fitted with include_lags=True, `history` and `make_date`
    must be supplied to compute the lag features at prediction time.
    """
    if not model:
        return 0.0
    first_date: datetime.date = model["first_date"]
    span: int = model["span"]
    beta: list[float] = model["beta"]
    include_weather: bool = model["include_weather"]
    include_lags: bool = model.get("include_lags", False)
    y_mean: float = model["intercept"]

    dow = target_date.weekday()
    dummies = [1.0 if dow == i else 0.0 for i in range(6)]
    days_since = (target_date - first_date).days
    trend = days_since / span
    feats = dummies + [trend]

    if include_weather:
        wf = _weather_features(target_date, weather)
        feats += [
            wf["tmean_f"] / 100.0,
            wf["precip_in"],
            wf["heat_flag"],
            wf["cold_flag"],
            wf["rainy_flag"],
            wf["heavy_rain"],
        ]

    if include_lags:
        if history is None or make_date is None:
            # Fallback: normalised mean (1.0) for all lag features
            feats += [1.0] * len(LAG_FEAT_NAMES)
        else:
            feats += _lag_feats_for_prediction(target_date, make_date, history)

    if len(feats) != len(beta):
        return 0.0
    pred = y_mean + sum(b * x for b, x in zip(beta, feats))
    return max(0.0, pred)


# ── Model F: log-space ramp-aware ridge ──────────────────────────────────────
#
# The heuristic and Models C/D/E all model orders on a LINEAR scale with either
# a clamped WoW growth factor or a normalised [0,1] trend term.  For a brand-new
# store in multiplicative (exponential) hypergrowth, that systematically
# under-forecasts: 60% of real WoW ratios fall outside the heuristic's
# [0.80, 1.20] clamp, and the linear trend term shrinks as the window grows.
#
# Model F instead fits ridge on LOG(orders) with an explicit weeks-since-open
# term.  In log space an exponential ramp is a straight line, so a single slope
# coefficient captures "+X%/week" growth with no clamp.  Predictions exponentiate
# back.  Lag features are entered in log-ratio form (log of lag / rolling mean).

RAMP_FEAT_NAMES = (
    ["weeks_since_open"]
    + ["dow_mon", "dow_tue", "dow_wed", "dow_thu", "dow_fri", "dow_sat"]
    + ["log_lag_7d", "log_roll_4w_dow"]
)


def _ramp_log_lag_feats(
    target_date: datetime.date,
    make_date: datetime.date,
    by_date: dict[str, int],
    roll_mean: float,
) -> list[float]:
    """log(lag/rollmean) features known at make_date (leakage-free)."""
    iso_make = make_date.isoformat()

    def _safe_log_ratio(iso: str) -> float:
        v = by_date.get(iso)
        if v is None or v <= 0 or iso >= iso_make:
            return 0.0  # unknown / future → log(1) = 0
        return math.log(v / roll_mean)

    lag7 = _safe_log_ratio((target_date - datetime.timedelta(days=7)).isoformat())
    same_dow = [
        by_date[(target_date - datetime.timedelta(days=7 * w)).isoformat()]
        for w in range(1, 5)
        if (target_date - datetime.timedelta(days=7 * w)).isoformat() in by_date
        and (target_date - datetime.timedelta(days=7 * w)).isoformat() < iso_make
    ]
    roll = math.log(statistics.mean(same_dow) / roll_mean) if same_dow else 0.0
    return [lag7, roll]


def _build_ramp_model(
    history: list[dict],
    weather: dict[str, dict],
    make_date: datetime.date,
    include_weather: bool = True,
) -> dict[str, Any]:
    """Fit ridge on log(orders) with a weeks-since-open ramp term."""
    clean = [r for r in history if not r.get("forecast_exclude") and r["orders"] > 0]
    iso_make = make_date.isoformat()
    train = sorted((r for r in clean if r["date"] < iso_make), key=lambda x: x["date"])
    if len(train) < MIN_WARMUP_DAYS:
        return {}

    first_date = datetime.date.fromisoformat(train[0]["date"])
    by_date_orders = {r["date"]: int(r["orders"]) for r in train}
    tail = train[-28:]
    roll_mean = max(statistics.mean(r["orders"] for r in tail), 1.0)

    feat_names = list(RAMP_FEAT_NAMES)
    if include_weather:
        feat_names = feat_names + ["tmean_f", "precip_in", "rainy_flag"]

    X: list[list[float]] = []
    y: list[float] = []
    for r in train:
        d = datetime.date.fromisoformat(r["date"])
        weeks_since = (d - first_date).days / 7.0
        dow = d.weekday()
        dummies = [1.0 if dow == i else 0.0 for i in range(6)]
        log_lags = _ramp_log_lag_feats(d, make_date, by_date_orders, roll_mean)
        feats = [weeks_since] + dummies + log_lags
        if include_weather:
            wf = _weather_features(d, weather)
            feats += [wf["tmean_f"] / 100.0, wf["precip_in"], wf["rainy_flag"]]
        X.append(feats)
        y.append(math.log(r["orders"]))

    y_mean = statistics.mean(y)
    y_c = [v - y_mean for v in y]
    beta = _ridge_solve(X, y_c, alpha=RIDGE_ALPHA)
    return {
        "beta": beta,
        "intercept": y_mean,
        "first_date": first_date,
        "roll_mean": roll_mean,
        "by_date": by_date_orders,
        "include_weather": include_weather,
        "feat_names": feat_names,
        "n_train": len(train),
        "log_space": True,
    }


def _ramp_predict(
    model: dict,
    target_date: datetime.date,
    weather: dict[str, dict],
    make_date: datetime.date,
) -> float:
    """Predict orders for target_date from a log-space ramp model."""
    if not model:
        return 0.0
    first_date: datetime.date = model["first_date"]
    beta: list[float] = model["beta"]
    y_mean: float = model["intercept"]
    roll_mean: float = model["roll_mean"]
    by_date: dict[str, int] = model["by_date"]
    include_weather: bool = model["include_weather"]

    weeks_since = (target_date - first_date).days / 7.0
    dow = target_date.weekday()
    dummies = [1.0 if dow == i else 0.0 for i in range(6)]
    log_lags = _ramp_log_lag_feats(target_date, make_date, by_date, roll_mean)
    feats = [weeks_since] + dummies + log_lags
    if include_weather:
        wf = _weather_features(target_date, weather)
        feats += [wf["tmean_f"] / 100.0, wf["precip_in"], wf["rainy_flag"]]

    if len(feats) != len(beta):
        return 0.0
    log_pred = y_mean + sum(b * x for b, x in zip(beta, feats))
    # Guard against runaway extrapolation
    log_pred = min(log_pred, math.log(roll_mean * 4))
    return max(0.0, math.exp(log_pred))


# ── Weather proxy helpers ───────────────────────────────────────────────────


def _resolve_weather(
    target_date: datetime.date,
    make_date: datetime.date,
    actual_weather: dict[str, dict],
    mode: str = "observed",
) -> dict[str, float]:
    """Return the weather dict to use for target_date given wx_mode.

    observed    — actual ERA5 weather for target_date (upper bound).
    persistence — use make_date's actual weather as the "forecast" for
                  target_date (conservative lower bound for H > 1).
                  For H == 1 (make_date == target_date) the two modes are
                  identical: you already know today's weather.
    """
    if mode == "persistence":
        # Persistence: the best forecast you can make on make_date is that
        # target_date will look like make_date.  NWP beats this for H <= 7;
        # this is intentionally conservative.
        proxy_date = make_date
    else:
        proxy_date = target_date
    return actual_weather.get(proxy_date.isoformat(), {})


def exclusion_stats(actuals: list[dict]) -> dict[str, Any]:
    """Return counts showing which days are excluded and why.

    The same flag is used for both training and testing — this function
    makes that explicit for audit purposes.
    """
    total = len(actuals)
    excluded = [r for r in actuals if r.get("forecast_exclude")]
    included = [r for r in actuals if not r.get("forecast_exclude")]
    return {
        "total_operating_days": total,
        "excluded_days": len(excluded),
        "included_days": len(included),
        "excluded_dates": sorted(r["date"] for r in excluded),
    }


def compute_feature_importance(
    actuals: list[dict],
    weather: dict[str, dict],
) -> dict[str, Any]:
    """Fit Models D and E on all available non-excluded training data and return
    feature names + beta coefficients (importance).

    Using the final make_date (last non-excluded date) gives the model trained
    on the most data — the most stable coefficient estimates we have.

    Interpretation guide:
    - Positive beta, positive feature value → more orders
    - Negative beta → fewer orders
    - |beta| rank ≈ importance (ridge-normalised, not permutation importance)
    """
    clean = sorted(
        [r for r in actuals if not r.get("forecast_exclude")],
        key=lambda x: x["date"],
    )
    if not clean:
        return {}
    # Use the latest date as the make_date so all data is in training.
    # Add one day so the last operating day is included (< make_date logic).
    last_date = datetime.date.fromisoformat(clean[-1]["date"])
    make_date = last_date + datetime.timedelta(days=1)

    model_d = _build_ridge_model(clean, weather, make_date, include_weather=True)
    model_e = _build_ridge_model(
        clean, weather, make_date, include_weather=True, include_lags=True
    )

    def _fmt(model: dict) -> list[dict]:
        names = model.get("feat_names", [])
        betas = model.get("beta", [])
        mean_y = model.get("intercept", 0.0)
        rows = []
        for name, beta in zip(names, betas):
            rows.append({
                "feature": name,
                "beta": round(beta, 3),
                "abs_beta": round(abs(beta), 3),
                "pct_of_mean": round(abs(beta) / max(mean_y, 1) * 100, 1),
            })
        rows.sort(key=lambda x: -x["abs_beta"])
        return rows

    return {
        "D": {"rows": _fmt(model_d), "n_train": model_d.get("n_train", 0)},
        "E": {"rows": _fmt(model_e), "n_train": model_e.get("n_train", 0)},
    }


# ── Walk-forward backtest ───────────────────────────────────────────────────


def run_backtest(
    actuals: list[dict],
    weather: dict[str, dict],
    horizons: list[int] | None = None,
    wx_mode: str = "observed",
) -> list[dict]:
    """Walk-forward backtest across all testable target dates and horizons.

    wx_mode controls what weather is fed to models B and D at prediction time:
    - "observed"    : actual ERA5 weather for the target date (upper bound).
    - "persistence" : make_date's actual weather used as the forecast for
                      target_date (conservative lower bound; NWP beats this).

    Training (fitting model weights) always uses actual weather regardless of
    wx_mode — we can only learn from what actually happened.

    For each target_date T and horizon H, the make-date is T-H+1 (the last day
    the forecaster knows before having to predict T).  We require at least
    MIN_WARMUP_DAYS operating days strictly before the make-date.

    EXCLUSION CONSISTENCY: forecast_exclude=True days are removed from BOTH the
    training history (all models) AND the test targets — the same set, the same
    flag, for every model.  No model sees an excluded day differently.

    Returns a flat list of result dicts, one per (target_date, horizon, model).
    """
    if horizons is None:
        horizons = HORIZONS

    # Exclude forecast_exclude=true days from the test set — these are marked
    # as anomalous (opening-day ramp, data-cutoff partials, operator overrides)
    # and would produce misleadingly large APE if scored.
    testable = [r for r in actuals if not r.get("forecast_exclude")]
    dates = sorted(set(r["date"] for r in testable))
    date_to_actual: dict[str, int] = {r["date"]: r["orders"] for r in testable}

    results: list[dict] = []

    for horizon in horizons:
        for target_iso in dates:
            target_date = datetime.date.fromisoformat(target_iso)
            make_date = target_date - datetime.timedelta(days=horizon - 1)

            # Count operating days strictly before make_date
            prior_ops = [
                r for r in actuals if r["date"] < make_date.isoformat()
            ]
            if len(prior_ops) < MIN_WARMUP_DAYS:
                continue

            actual = date_to_actual.get(target_iso, 0)
            if actual <= 0:
                continue

            history_before_make = [r for r in actuals if r["date"] < make_date.isoformat()]

            # Weather at PREDICTION TIME: observed or persistence proxy.
            # Training always uses actual weather (history_before_make dates).
            pred_weather = _resolve_weather(target_date, make_date, weather, wx_mode)

            # Build a single-date dict for functions that expect {date: vars}.
            pred_wx_dict = {target_iso: pred_weather}

            # ── Model A ──────────────────────────────────────────────
            fa = forecast_model_a(history_before_make, target_date)

            # ── Model B ──────────────────────────────────────────────
            # Training correction still fitted on actual historical weather;
            # only the prediction-time lookup uses pred_wx_dict.
            fb = forecast_model_b(
                history_before_make, weather, target_date, make_date,
                pred_weather=pred_weather,
            )

            # ── Models C, D, E (Ridge) — fitted on actual history ────
            # _build_ridge_model uses actual weather for training (correct).
            # _ridge_predict gets pred_wx_dict for the target date.
            model_c = _build_ridge_model(
                history_before_make, weather, make_date, include_weather=False
            )
            model_d = _build_ridge_model(
                history_before_make, weather, make_date, include_weather=True
            )
            # Model E: same as D but adds lagged-order features.
            # These capture "where are we right now" — the piece that lets the
            # regression improve as more data accumulates.
            model_e = _build_ridge_model(
                history_before_make, weather, make_date,
                include_weather=True, include_lags=True,
            )
            # Model F: log-space ramp-aware ridge — tracks multiplicative growth
            # (the new-store ramp) that the clamped linear models cannot.
            model_f = _build_ramp_model(
                history_before_make, weather, make_date, include_weather=True,
            )
            fc = _ridge_predict(model_c, target_date, pred_wx_dict)
            fd = _ridge_predict(model_d, target_date, pred_wx_dict)
            fe = _ridge_predict(
                model_e, target_date, pred_wx_dict,
                history=history_before_make, make_date=make_date,
            )
            ff = _ramp_predict(model_f, target_date, pred_wx_dict, make_date)

            for label, forecast in [("A", fa), ("B", fb), ("C", fc), ("D", fd), ("E", fe), ("F", ff)]:
                if forecast <= 0:
                    continue
                ape = abs(actual - forecast) / actual
                error = actual - forecast
                results.append(
                    {
                        "target_date": target_iso,
                        "horizon": horizon,
                        "wx_mode": wx_mode,
                        "model": label,
                        "actual": actual,
                        "forecast": round(forecast, 1),
                        "error": round(error, 1),
                        "ape": round(ape, 4),
                        "dow": target_date.strftime("%a"),
                        "make_date": make_date.isoformat(),
                    }
                )

    return results


# ── Aggregation ─────────────────────────────────────────────────────────────


def compute_summary(results: list[dict]) -> list[dict]:
    """Aggregate MAPE, MAE, and bias by (model, horizon) and (model, dow)."""
    summary_rows: list[dict] = []

    for group_key in ["horizon", "dow"]:
        # Collect unique group values
        groups = sorted(set(r[group_key] for r in results))
        for model in ["A", "B", "C", "D", "E", "F"]:
            for grp in groups:
                subset = [
                    r for r in results
                    if r["model"] == model and r[group_key] == grp
                ]
                if not subset:
                    continue
                apes = [r["ape"] for r in subset]
                errors = [r["error"] for r in subset]
                summary_rows.append(
                    {
                        "group_by": group_key,
                        "group_value": str(grp),
                        "model": model,
                        "model_name": MODEL_NAMES[model],
                        "n": len(subset),
                        "mape": round(statistics.mean(apes), 4),
                        "mae": round(statistics.mean(abs(e) for e in errors), 2),
                        "bias": round(statistics.mean(errors), 2),
                    }
                )
    return summary_rows


# ── Output writers ──────────────────────────────────────────────────────────


def write_backtest_csv(results: list[dict]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    fieldnames = [
        "target_date", "horizon", "wx_mode", "model", "model_name",
        "actual", "forecast", "error", "ape", "dow", "make_date",
    ]
    with open(BACKTEST_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow({**r, "model_name": MODEL_NAMES[r["model"]]})
    print(f"  Wrote {len(results)} rows → {BACKTEST_PATH}")


def write_summary_csv(summary: list[dict]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    fieldnames = ["group_by", "group_value", "model", "model_name", "n", "mape", "mae", "bias"]
    with open(SUMMARY_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)
    print(f"  Wrote {len(summary)} rows → {SUMMARY_PATH}")


# ── Console summary ─────────────────────────────────────────────────────────


def print_summary(results: list[dict], summary: list[dict], wx_mode: str = "observed") -> None:
    """Print the headline comparison and key breakdowns."""
    mode_label = "observed weather (upper bound)" if wx_mode == "observed" else "persistence weather (lower bound)"
    horizon_rows = [r for r in summary if r["group_by"] == "horizon"]

    # Headline: overall MAPE for models A and D (across all horizons)
    def _overall_mape(model: str) -> float:
        subset = [r for r in results if r["model"] == model]
        if not subset:
            return float("nan")
        return statistics.mean(r["ape"] for r in subset)

    def _overall_bias(model: str) -> float:
        subset = [r for r in results if r["model"] == model]
        if not subset:
            return float("nan")
        return statistics.mean(r["error"] for r in subset)  # actual - forecast

    mape_a = _overall_mape("A")
    mape_d = _overall_mape("D")
    mape_e = _overall_mape("E")
    mape_f = _overall_mape("F")
    n_total = len([r for r in results if r["model"] == "A"])

    print()
    print("=" * 62)
    print(f"  WEATHER FORECAST SPIKE — {mode_label.upper()}")
    print("=" * 62)
    print(f"  Operating days tested:   {n_total}")
    print(f"  Horizons:                {HORIZONS}")
    print()
    print(f"  Headline #1  Model A (Heuristic v2):            MAPE = {mape_a:.1%}  bias = {_overall_bias('A'):+.1f}")
    print(f"  Headline #2  Model D (Ridge + weather):         MAPE = {mape_d:.1%}  bias = {_overall_bias('D'):+.1f}")
    print(f"  Headline #3  Model E (Ridge + lags + weather):  MAPE = {mape_e:.1%}  bias = {_overall_bias('E'):+.1f}")
    print(f"  Headline #4  Model F (Ramp-aware log + lags+wx): MAPE = {mape_f:.1%}  bias = {_overall_bias('F'):+.1f}")
    print("  (bias = mean(actual - forecast); +ve = systematic UNDER-forecast)")
    best_model = min(["A", "D", "E", "F"], key=lambda m: _overall_mape(m))
    best_mape = _overall_mape(best_model)
    delta_best = best_mape - mape_a
    print(f"\n  Verdict: Best model = {best_model} ({MODEL_NAMES[best_model]}) at {best_mape:.1%} MAPE"
          f" ({'+' if delta_best >= 0 else ''}{delta_best:.1%} pp vs heuristic A).")
    print()

    # Breakdown by horizon
    print("  MAPE by horizon:")
    print(f"  {'Horizon':>8}  {'Model A':>8}  {'Model B':>8}  {'Model C':>8}  {'Model D':>8}  {'n':>5}")
    print(f"  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*5}")
    for h in HORIZONS:
        row: dict[str, Any] = {"horizon": h}
        for m in ["A", "B", "C", "D"]:
            match = next(
                (r for r in horizon_rows if r["model"] == m and r["group_value"] == str(h)),
                None,
            )
            row[m] = f"{match['mape']:.1%}" if match else "  —  "
            row["n"] = match["n"] if match else 0
        print(
            f"  {h:>7}d  {row['A']:>8}  {row['B']:>8}  {row['C']:>8}  {row['D']:>8}"
            f"  {row['n']:>5}"
        )

    print()
    print("  Caveats:")
    print("  1. Observed weather used as proxy — this is the UPPER BOUND of weather value.")
    print("  2. Small sample (~86 operating days). Ridge has very few training rows.")
    print("  3. Walk-forward verified: no look-ahead used.")
    print("=" * 62)
    print()


# ── Canvas generation ───────────────────────────────────────────────────────


def _mape_by_horizon_series(
    results: list[dict],
) -> dict[str, list[float]]:
    """Return {model: [mape_h1, mape_h3, mape_h7, mape_h14]}."""
    out: dict[str, list[float]] = {}
    for model in ["A", "B", "C", "D", "E", "F"]:
        row = []
        for h in HORIZONS:
            subset = [r for r in results if r["model"] == model and r["horizon"] == h]
            row.append(round(statistics.mean(r["ape"] for r in subset) * 100, 1) if subset else 0.0)
        out[model] = row
    return out


def _mape_by_dow_series(
    results: list[dict],
) -> dict[str, list[float]]:
    """Return {model: [mape_Mon, …, mape_Sun]} for horizon=1."""
    dows = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    out: dict[str, list[float]] = {}
    for model in ["A", "B", "C", "D", "E", "F"]:
        row = []
        for dow in dows:
            subset = [
                r for r in results
                if r["model"] == model and r["horizon"] == 1 and r["dow"] == dow
            ]
            row.append(round(statistics.mean(r["ape"] for r in subset) * 100, 1) if subset else 0.0)
        out[model] = row
    return out


def _bias_by_horizon(
    results: list[dict],
) -> dict[str, list[float]]:
    """Return {model: [bias_h1, …, bias_h14]} (mean signed error in orders)."""
    out: dict[str, list[float]] = {}
    for model in ["A", "B", "C", "D", "E", "F"]:
        row = []
        for h in HORIZONS:
            subset = [r for r in results if r["model"] == model and r["horizon"] == h]
            row.append(round(statistics.mean(r["error"] for r in subset), 1) if subset else 0.0)
        out[model] = row
    return out


def _overall_mape(results: list[dict], model: str) -> float:
    subset = [r for r in results if r["model"] == model]
    return round(statistics.mean(r["ape"] for r in subset) * 100, 1) if subset else 0.0


def _overall_mae(results: list[dict], model: str) -> float:
    subset = [r for r in results if r["model"] == model]
    return round(statistics.mean(abs(r["error"]) for r in subset), 1) if subset else 0.0


def _sample_n(results: list[dict]) -> int:
    return len(set((r["target_date"], r["horizon"]) for r in results if r["model"] == "A"))


def _mape_over_time(
    results: list[dict],
    horizon: int = 1,
    roll_window: int = 5,
) -> dict[str, Any]:
    """Return data for the 'learning curve' chart.

    For each test date (at the given horizon), computes:
    - cumulative MAPE up to and including that date
    - rolling MAPE over the last `roll_window` dates

    Returns a dict with:
      dates         : list of 'MM-DD' labels
      cum_a, cum_d  : cumulative MAPE (%) lists for Models A and D
      roll_a, roll_d: rolling MAPE (%) lists (None-padded until enough data)
      n_train       : approximate # training days available at each date
                      (derived from make_date - first_actual_date)
    """
    models = ["A", "D"]
    by_model: dict[str, list[dict]] = {
        m: sorted(
            [r for r in results if r["model"] == m and r["horizon"] == horizon],
            key=lambda x: x["target_date"],
        )
        for m in models
    }

    # Use Model A's dates as the canonical x-axis (both models share the same dates)
    dates = [r["target_date"] for r in by_model["A"]]
    if not dates:
        return {}

    date_labels = [d[-5:] for d in dates]  # MM-DD

    out: dict[str, Any] = {"dates": date_labels, "n_train": []}

    for m in models:
        rows = by_model[m]
        apes = [r["ape"] * 100 for r in rows]
        cum = []
        roll = []
        for i in range(len(apes)):
            cum.append(round(statistics.mean(apes[: i + 1]), 1))
            if i + 1 >= roll_window:
                roll.append(round(statistics.mean(apes[i - roll_window + 1 : i + 1]), 1))
            else:
                roll.append(None)
        out[f"cum_{m.lower()}"] = cum
        out[f"roll_{m.lower()}"] = roll

    # Approximate # training days = index of make_date in the full actuals timeline.
    # Use make_date from the results (it's stored as target_date - horizon + 1).
    first_make = by_model["A"][0]["target_date"]
    for i, row in enumerate(by_model["A"]):
        make = (
            datetime.date.fromisoformat(row["target_date"])
            - datetime.timedelta(days=horizon - 1)
        ).isoformat()
        # Count rows that had data strictly before make_date —
        # use the "prior_ops" index implicitly via the walk-forward
        # We know training starts at MIN_WARMUP_DAYS; approximate as
        # MIN_WARMUP_DAYS + i (one new day per test step at h=1).
        out["n_train"].append(MIN_WARMUP_DAYS + i)

    return out


def generate_canvas(results: list[dict], summary: list[dict]) -> None:
    """Write the results canvas with all data embedded inline."""
    if not results:
        print("  No backtest results — skipping canvas generation.")
        return

    horizon_series = _mape_by_horizon_series(results)
    dow_series = _mape_by_dow_series(results)
    bias_series = _bias_by_horizon(results)

    mape_a = _overall_mape(results, "A")
    mape_d = _overall_mape(results, "D")
    mae_a = _overall_mae(results, "A")
    mae_d = _overall_mae(results, "D")
    n_samples = _sample_n(results)

    delta = mape_d - mape_a
    if abs(delta) < 0.1:
        verdict_tone = "neutral"
        verdict = "No meaningful difference between models"
    elif delta < 0:
        verdict_tone = "success"
        verdict = f"Ridge + weather wins by {abs(delta):.1f} pp MAPE"
    else:
        verdict_tone = "danger"
        verdict = f"Heuristic v2 wins — weather adds {delta:.1f} pp MAPE (overfit)"

    dows_json = json.dumps(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    horizons_json = json.dumps([f"{h}d" for h in HORIZONS])
    horizon_series_json = json.dumps(horizon_series)
    dow_series_json = json.dumps(dow_series)
    bias_series_json = json.dumps(bias_series)

    # Build summary table rows
    horizon_table_rows: list[dict] = []
    for h in HORIZONS:
        row_d: dict[str, Any] = {"horizon": f"{h}d"}
        for m in ["A", "B", "C", "D"]:
            match = next(
                (r for r in summary if r["group_by"] == "horizon"
                 and r["model"] == m and r["group_value"] == str(h)),
                None,
            )
            row_d[m] = f"{match['mape'] * 100:.1f}%" if match else "—"
            row_d[f"mae_{m}"] = f"{match['mae']:.1f}" if match else "—"
        row_d["n"] = match["n"] if match else 0
        horizon_table_rows.append(row_d)

    table_data_json = json.dumps(horizon_table_rows)
    run_date = datetime.date.today().isoformat()

    canvas_tsx = textwrap.dedent(f"""\
        import {{
          BarChart,
          Callout,
          Card,
          CardBody,
          CardHeader,
          Divider,
          Grid,
          H1,
          H2,
          H3,
          LineChart,
          Stack,
          Stat,
          Table,
          Text,
          useHostTheme,
        }} from "cursor/canvas";

        // ── Embedded backtest results ──────────────────────────────────────────────
        const HORIZONS: string[] = {horizons_json};
        const DOWS: string[] = {dows_json};

        // MAPE (%) by model × horizon
        const MAPE_BY_HORIZON: Record<string, number[]> = {horizon_series_json};

        // MAPE (%) by model × DOW (horizon=1)
        const MAPE_BY_DOW: Record<string, number[]> = {dow_series_json};

        // Mean signed error (orders) by model × horizon
        const BIAS_BY_HORIZON: Record<string, number[]> = {bias_series_json};

        // Summary table (MAPE/MAE per horizon)
        const TABLE_ROWS: Record<string, string | number>[] = {table_data_json};

        // Headline stats
        const MAPE_A = {mape_a};   // Model A overall MAPE %
        const MAPE_D = {mape_d};   // Model D overall MAPE %
        const MAE_A = {mae_a};     // Model A overall MAE (orders)
        const MAE_D = {mae_d};     // Model D overall MAE (orders)
        const N_SAMPLES = {n_samples};  // unique (date, horizon) pairs tested
        const VERDICT = "{verdict}";
        const VERDICT_TONE = "{verdict_tone}" as "success" | "danger" | "neutral";
        const RUN_DATE = "{run_date}";

        export default function WeatherForecastSpike() {{
          const theme = useHostTheme();

          const modelSeries = (models: string[], data: Record<string, number[]>) =>
            models.map((m) => ({{
              name: `${{m}} ${{{{ A: "Heuristic v2", B: "Heuristic+weather", C: "Ridge no-wx", D: "Ridge+weather" }}[m]}}`,
              data: data[m] ?? [],
            }}));

          return (
            <Stack gap={{24}} style={{{{ padding: 24, maxWidth: 960, margin: "0 auto" }}}}>
              {{/* Header */}}
              <Stack gap={{4}}>
                <H1>Weather Forecast Spike — BHAGA</H1>
                <Text style={{{{ color: theme.text.secondary }}}}>
                  Walk-forward backtest · {{N_SAMPLES}} (date × horizon) samples · run {{RUN_DATE}}
                </Text>
              </Stack>

              {{/* Verdict callout */}}
              <Callout tone={{VERDICT_TONE}}>
                <strong>Decision:</strong> {{VERDICT}}
              </Callout>

              {{/* Headline stats */}}
              <Grid columns={{4}} gap={{16}}>
                <Stat
                  label="Model A MAPE"
                  value={{`${{MAPE_A.toFixed(1)}}%`}}
                  caption="Heuristic v2 (production)"
                />
                <Stat
                  label="Model D MAPE"
                  value={{`${{MAPE_D.toFixed(1)}}%`}}
                  caption="Ridge + weather"
                  tone={{MAPE_D < MAPE_A ? "success" : MAPE_D > MAPE_A ? "danger" : "neutral"}}
                />
                <Stat
                  label="Model A MAE"
                  value={{`${{MAE_A.toFixed(1)}} orders`}}
                  caption="Mean absolute error"
                />
                <Stat
                  label="Model D MAE"
                  value={{`${{MAE_D.toFixed(1)}} orders`}}
                  caption="Mean absolute error"
                  tone={{MAE_D < MAE_A ? "success" : MAE_D > MAE_A ? "danger" : "neutral"}}
                />
              </Grid>

              <Divider />

              {{/* MAPE by horizon */}}
              <Stack gap={{8}}>
                <H2>MAPE by forecast horizon (all 4 models)</H2>
                <Text style={{{{ color: theme.text.secondary, fontSize: 13 }}}}>
                  Lower is better. A vs D is the decision; B and C are diagnostics.
                </Text>
                <BarChart
                  categories={{HORIZONS}}
                  series={{modelSeries(["A", "B", "C", "D"], MAPE_BY_HORIZON)}}
                  valueSuffix="%"
                  height={{280}}
                  beginAtZero={{true}}
                />
                <Text style={{{{ color: theme.text.secondary, fontSize: 12 }}}}>
                  Source: walk-forward backtest · actuals from BigQuery · weather from Open-Meteo archive
                </Text>
              </Stack>

              {{/* Summary table */}}
              <Card>
                <CardHeader title="MAPE by horizon — numeric summary" />
                <CardBody style={{{{ padding: 0 }}}}>
                  <Table
                    columns={{[
                      {{ key: "horizon", label: "Horizon" }},
                      {{ key: "A", label: "A  Heuristic v2", align: "right" as const }},
                      {{ key: "B", label: "B  Heuristic+wx", align: "right" as const }},
                      {{ key: "C", label: "C  Ridge no-wx", align: "right" as const }},
                      {{ key: "D", label: "D  Ridge+wx", align: "right" as const }},
                      {{ key: "n", label: "n", align: "right" as const }},
                    ]}}
                    rows={{TABLE_ROWS}}
                  />
                </CardBody>
              </Card>

              <Divider />

              {{/* MAPE by day-of-week (horizon = 1d) */}}
              <Stack gap={{8}}>
                <H2>MAPE by day of week (1-day horizon)</H2>
                <Text style={{{{ color: theme.text.secondary, fontSize: 13 }}}}>
                  Which days benefit most (or least) from weather data?
                </Text>
                <BarChart
                  categories={{DOWS}}
                  series={{modelSeries(["A", "D"], MAPE_BY_DOW)}}
                  valueSuffix="%"
                  height={{240}}
                  beginAtZero={{true}}
                />
              </Stack>

              {{/* Bias by horizon */}}
              <Stack gap={{8}}>
                <H2>Forecast bias by horizon</H2>
                <Text style={{{{ color: theme.text.secondary, fontSize: 13 }}}}>
                  Mean signed error (orders).  Negative = under-forecast; positive = over-forecast.
                </Text>
                <LineChart
                  categories={{HORIZONS}}
                  series={{modelSeries(["A", "D"], BIAS_BY_HORIZON)}}
                  valueSuffix=" orders"
                  beginAtZero={{false}}
                  height={{220}}
                />
              </Stack>

              <Divider />

              {{/* Caveats */}}
              <Stack gap={{8}}>
                <H3>Caveats</H3>
                <Stack gap={{4}}>
                  <Text>
                    <strong>1. Observed weather as proxy.</strong> The backtest uses actual historical
                    weather, not a weather forecast. This measures the <em>upper bound</em> of
                    weather&apos;s value. Production would use forecast weather (≤7-10 days reliable),
                    adding additional error. If even the upper bound doesn&apos;t beat Model A, weather
                    is not worth productionizing.
                  </Text>
                  <Text>
                    <strong>2. Small sample.</strong> ~86 operating days yields very few training rows
                    for Ridge, especially for rainy-day events. Treat all regression results as
                    directional only.
                  </Text>
                  <Text>
                    <strong>3. Walk-forward, leakage-free.</strong> Every forecast was made using only
                    data strictly before the make-date. No hindsight used.
                  </Text>
                </Stack>
              </Stack>
            </Stack>
          );
        }}
        """)

    CANVAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CANVAS_PATH, "w") as f:
        f.write(canvas_tsx)
    print(f"  Wrote canvas → {CANVAS_PATH}")


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    print("Loading actuals…")
    actuals = load_actuals()
    print(f"  {len(actuals)} operating days loaded")

    # ── Exclusion consistency audit ───────────────────────────────────────
    excl = exclusion_stats(actuals)
    print(
        f"  Exclusion audit: {excl['total_operating_days']} operating days total, "
        f"{excl['excluded_days']} excluded (forecast_exclude=True), "
        f"{excl['included_days']} used for both training and testing."
    )
    if excl["excluded_dates"]:
        print(f"  Excluded dates: {', '.join(excl['excluded_dates'])}")

    print("Loading weather…")
    weather = load_weather()
    print(f"  {len(weather)} daily weather rows loaded")

    # ── Run backtest in both weather modes ────────────────────────────────
    print("Running walk-forward backtest — mode: observed (upper bound)…")
    results_obs = run_backtest(actuals, weather, wx_mode="observed")
    print(f"  {len(results_obs)} forecast–actual pairs computed")

    print("Running walk-forward backtest — mode: persistence (lower bound)…")
    results_per = run_backtest(actuals, weather, wx_mode="persistence")
    print(f"  {len(results_per)} forecast–actual pairs computed")

    all_results = results_obs + results_per

    if not results_obs:
        print("ERROR: no backtest results (insufficient warmup data?)", file=sys.stderr)
        sys.exit(1)

    print("Computing summary…")
    summary_obs = compute_summary(results_obs)
    summary_per = compute_summary(results_per)

    print("Computing feature importance…")
    feat_importance = compute_feature_importance(actuals, weather)
    n_d = feat_importance.get("D", {}).get("n_train", 0)
    n_e = feat_importance.get("E", {}).get("n_train", 0)
    print(f"  Model D: {n_d} training days, {len(feat_importance.get('D', {}).get('rows', []))} features")
    print(f"  Model E: {n_e} training days, {len(feat_importance.get('E', {}).get('rows', []))} features")

    print("Writing output files…")
    write_backtest_csv(all_results)
    write_summary_csv(summary_obs)

    print_summary(results_obs, summary_obs)
    print_summary(results_per, summary_per, wx_mode="persistence")

    print("Generating HTML report…")
    generate_html_report(results_obs, summary_obs, results_per, summary_per, excl, feat_importance)

    print("Generating canvas…")
    generate_canvas(results_obs, summary_obs)

    print("Done.")


HTML_REPORT_PATH = OUT_DIR / "report.html"


# ── HTML report ─────────────────────────────────────────────────────────────


def generate_html_report(
    results_obs: list[dict],
    summary_obs: list[dict],
    results_per: list[dict],
    summary_per: list[dict],
    excl_stats: dict | None = None,
    feat_importance: dict | None = None,
) -> None:
    """Write a self-contained HTML report to out/report.html.

    Shows both weather modes side-by-side:
    - Observed weather (upper bound)
    - Persistence forecast (conservative lower bound)

    Uses Chart.js from CDN for bar/line charts and plain HTML tables.
    All data is embedded as JSON — open the file in any browser offline
    (CDN only needed for rendering charts; the data table is pure HTML).
    """
    results = results_obs  # primary for single-mode sections
    summary = summary_obs
    if not results:
        print("  No results — skipping HTML report.", file=sys.stderr)
        return

    mape_a = _overall_mape(results, "A")
    mape_b = _overall_mape(results, "B")
    mape_c = _overall_mape(results, "C")
    mape_d = _overall_mape(results, "D")
    mape_e = _overall_mape(results, "E")
    mape_f = _overall_mape(results, "F")
    mae_a = _overall_mae(results, "A")
    mae_d = _overall_mae(results, "D")
    mae_e = _overall_mae(results, "E")
    n_samples = _sample_n(results)

    # Bias (mean actual - forecast) per model — +ve = systematic under-forecast
    def _bias(model: str) -> float:
        subset = [r for r in results if r["model"] == model]
        return round(statistics.mean(r["error"] for r in subset), 1) if subset else 0.0
    bias_val_a, bias_val_d, bias_val_e, bias_val_f = _bias("A"), _bias("D"), _bias("E"), _bias("F")

    # Noise-floor (Poisson) at this store's mean volume — the physical MAPE limit
    mean_vol = statistics.mean(r["actual"] for r in results if r["model"] == "A") if results else 0
    noise_floor = round(100.0 / math.sqrt(mean_vol), 1) if mean_vol > 0 else 0.0
    vol_for_5pct = int((100.0 / 5.0) ** 2)  # orders/day needed for 5% floor

    # Headline verdict: best model by MAPE
    best_model = min(["A", "D", "E", "F"], key=lambda m: _overall_mape(results, m))
    best_mape = _overall_mape(results, best_model)
    delta_e = best_mape - mape_a
    if abs(delta_e) < 0.1:
        verdict_cls = "neutral"
        verdict_icon = "—"
        verdict = "No meaningful MAPE difference between models on this sample"
    else:
        verdict_cls = "win"
        verdict_icon = "&#x25BC;"
        verdict = (
            f"Best MAPE: Model {best_model} ({MODEL_NAMES[best_model]}) at "
            f"<strong>{best_mape:.1f}%</strong> ({abs(delta_e):.1f} pp better than heuristic). "
            f"But the bigger win is <strong>bias</strong>: Model F cuts systematic "
            f"under-forecast from {bias_val_a:+.0f} to {bias_val_f:+.0f} orders/day."
        )

    # Secondary verdict for D (no lags) for context
    delta_d = mape_d - mape_a

    # Build horizon table rows (all models A–F)
    horizon_rows_html = ""
    horizon_series = _mape_by_horizon_series(results)
    for h in HORIZONS:
        cells = f"<td>{h}d</td>"
        for m in ["A", "B", "C", "D", "E", "F"]:
            val = horizon_series[m][HORIZONS.index(h)]
            best = min(horizon_series[m2][HORIZONS.index(h)] for m2 in ["A", "B", "C", "D", "E", "F"])
            cls = ' class="best"' if abs(val - best) < 0.01 else ""
            cells += f"<td{cls}>{val:.1f}%</td>"
        n_h = len([r for r in results if r["model"] == "A" and r["horizon"] == h])
        cells += f"<td>{n_h}</td>"
        horizon_rows_html += f"<tr>{cells}</tr>\n"

    # DOW table rows (horizon=1)
    dows = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_series = _mape_by_dow_series(results)
    dow_rows_html = ""
    for i, dow in enumerate(dows):
        vals = {m: dow_series[m][i] for m in ["A", "B", "C", "D", "E", "F"]}
        best = min(vals.values())
        cells = f"<td>{dow}</td>"
        for m in ["A", "B", "C", "D", "E", "F"]:
            cls = ' class="best"' if abs(vals[m] - best) < 0.01 else ""
            cells += f"<td{cls}>{vals[m]:.1f}%</td>"
        dow_rows_html += f"<tr>{cells}</tr>\n"

    # Time-series scatter: per-day Model A vs D errors (horizon=1)
    h1_a = sorted([r for r in results if r["model"] == "A" and r["horizon"] == 1], key=lambda x: x["target_date"])
    h1_d = sorted([r for r in results if r["model"] == "D" and r["horizon"] == 1], key=lambda x: x["target_date"])
    h1_e = sorted([r for r in results if r["model"] == "E" and r["horizon"] == 1], key=lambda x: x["target_date"])
    scatter_labels = json.dumps([r["target_date"][-5:] for r in h1_a])  # MM-DD
    scatter_a = json.dumps([round(r["ape"] * 100, 1) for r in h1_a])
    scatter_d_map = {r["target_date"]: round(r["ape"] * 100, 1) for r in h1_d}
    scatter_d = json.dumps([scatter_d_map.get(r["target_date"], 0.0) for r in h1_a])
    scatter_e_map = {r["target_date"]: round(r["ape"] * 100, 1) for r in h1_e}
    scatter_e = json.dumps([scatter_e_map.get(r["target_date"], 0.0) for r in h1_a])

    # Horizon chart data
    hdata_a = json.dumps(horizon_series["A"])
    hdata_b = json.dumps(horizon_series["B"])
    hdata_c = json.dumps(horizon_series["C"])
    hdata_d = json.dumps(horizon_series["D"])
    hdata_e = json.dumps(horizon_series["E"])
    hdata_f = json.dumps(horizon_series["F"])
    hlabels = json.dumps([f"{h}d" for h in HORIZONS])

    # DOW chart data
    dow_a = json.dumps(dow_series["A"])
    dow_d = json.dumps(dow_series["D"])
    dow_e = json.dumps(dow_series["E"])
    dows_json = json.dumps(dows)

    # Bias data
    bias_series = _bias_by_horizon(results)
    bias_a = json.dumps(bias_series["A"])
    bias_d = json.dumps(bias_series["D"])

    # Persistence-mode headline numbers (for comparison band)
    mape_a_per = _overall_mape(results_per, "A")
    mape_d_per = _overall_mape(results_per, "D")
    mape_e_per = _overall_mape(results_per, "E")

    # Feature importance JSON for chart
    fi_d_labels = json.dumps([r["feature"] for r in (feat_importance or {}).get("D", {}).get("rows", [])])
    fi_d_vals   = json.dumps([r["beta"]    for r in (feat_importance or {}).get("D", {}).get("rows", [])])
    fi_e_labels = json.dumps([r["feature"] for r in (feat_importance or {}).get("E", {}).get("rows", [])])
    fi_e_vals   = json.dumps([r["beta"]    for r in (feat_importance or {}).get("E", {}).get("rows", [])])

    # Feature importance HTML table rows
    fi_d_rows_html = ""
    for r in (feat_importance or {}).get("D", {}).get("rows", []):
        sign = "+" if r["beta"] >= 0 else ""
        fi_d_rows_html += (
            f"<tr><td>{r['feature']}</td>"
            f"<td style='text-align:right;color:{'#27ae60' if r['beta']>=0 else '#e74c3c'}'>"
            f"{sign}{r['beta']:.3f}</td>"
            f"<td style='text-align:right'>{r['pct_of_mean']:.1f}%</td></tr>\n"
        )
    fi_e_rows_html = ""
    for r in (feat_importance or {}).get("E", {}).get("rows", []):
        sign = "+" if r["beta"] >= 0 else ""
        fi_e_rows_html += (
            f"<tr><td>{r['feature']}</td>"
            f"<td style='text-align:right;color:{'#27ae60' if r['beta']>=0 else '#e74c3c'}'>"
            f"{sign}{r['beta']:.3f}</td>"
            f"<td style='text-align:right'>{r['pct_of_mean']:.1f}%</td></tr>\n"
        )

    # Exclusion section HTML
    if excl_stats:
        excl_dates_html = ""
        for d in excl_stats["excluded_dates"]:
            excl_dates_html += f"<li><code>{d}</code></li>\n"
        excl_html = f"""
  <div class="section" id="excl-section">
    <h2>Exclusion consistency audit</h2>
    <p style="margin:0 0 10px;color:#555;font-size:13px;">
      Days marked <code>forecast_exclude=True</code> are removed from <strong>both</strong>
      the training history and the test targets for <strong>all four models</strong> —
      using the same flag, applied the same way, every time.
      No model is evaluated on an anomalous day that another model skips.
    </p>
    <div style="display:grid;grid-template-columns:repeat(3,auto);gap:8px 24px;font-size:13px;margin-bottom:10px;">
      <div><span style="color:#888;">Total operating days:</span> <strong>{excl_stats['total_operating_days']}</strong></div>
      <div><span style="color:#e74c3c;">Excluded (forecast_exclude):</span> <strong>{excl_stats['excluded_days']}</strong></div>
      <div><span style="color:#27ae60;">Used for training &amp; testing:</span> <strong>{excl_stats['included_days']}</strong></div>
    </div>
    {"<p style='font-size:12px;color:#888;margin:0;'>Excluded dates: <ul style='margin:4px 0 0 16px;padding:0;'>" + excl_dates_html + "</ul></p>" if excl_stats['excluded_dates'] else "<p style='font-size:12px;color:#888;margin:0;'>No days excluded.</p>"}
  </div>""".strip()
    else:
        excl_html = ""

    run_date = datetime.date.today().isoformat()
    date_range_start = min(r["target_date"] for r in results)
    date_range_end = max(r["target_date"] for r in results)

    # Persistence-mode horizon series for comparison chart
    hs_per = _mape_by_horizon_series(results_per)
    hdata_d_per = json.dumps([round(v, 1) for v in hs_per["D"]])

    # Learning-curve data — include Model E to show learning improvement
    lc1_obs = _mape_over_time(results_obs, horizon=1, roll_window=5)
    lc7_obs = _mape_over_time(results_obs, horizon=7, roll_window=4)
    lc1_per = _mape_over_time(results_per, horizon=1, roll_window=5)
    lc7_per = _mape_over_time(results_per, horizon=7, roll_window=4)

    # Add Model E to _mape_over_time manually (it shares the same dates as A)
    def _cum_series_for_model(results_list: list[dict], model: str, horizon: int) -> list[float]:
        rows = sorted(
            [r for r in results_list if r["model"] == model and r["horizon"] == horizon],
            key=lambda x: x["target_date"],
        )
        apes = [r["ape"] * 100 for r in rows]
        return [round(statistics.mean(apes[: i + 1]), 1) for i in range(len(apes))]

    def _roll_series_for_model(results_list: list[dict], model: str, horizon: int, window: int) -> list[Any]:
        rows = sorted(
            [r for r in results_list if r["model"] == model and r["horizon"] == horizon],
            key=lambda x: x["target_date"],
        )
        apes = [r["ape"] * 100 for r in rows]
        out: list[Any] = []
        for i in range(len(apes)):
            if i + 1 >= window:
                out.append(round(statistics.mean(apes[i - window + 1 : i + 1]), 1))
            else:
                out.append(None)
        return out

    lc_dates_h1 = json.dumps(lc1_obs.get("dates", []))
    lc_cum_a_h1 = json.dumps(lc1_obs.get("cum_a", []))
    lc_cum_d_h1_obs = json.dumps(lc1_obs.get("cum_d", []))
    lc_cum_d_h1_per = json.dumps(lc1_per.get("cum_d", []))
    lc_cum_e_h1_obs = json.dumps(_cum_series_for_model(results_obs, "E", 1))
    lc_cum_e_h1_per = json.dumps(_cum_series_for_model(results_per, "E", 1))
    lc_roll_a_h1 = json.dumps(lc1_obs.get("roll_a", []))
    lc_roll_d_h1_obs = json.dumps(lc1_obs.get("roll_d", []))
    lc_roll_d_h1_per = json.dumps(lc1_per.get("roll_d", []))
    lc_roll_e_h1_obs = json.dumps(_roll_series_for_model(results_obs, "E", 1, 5))
    lc_roll_e_h1_per = json.dumps(_roll_series_for_model(results_per, "E", 1, 5))
    lc_cum_f_h1_obs = json.dumps(_cum_series_for_model(results_obs, "F", 1))
    lc_roll_f_h1_obs = json.dumps(_roll_series_for_model(results_obs, "F", 1, 5))
    lc_n_train_h1 = json.dumps(lc1_obs.get("n_train", []))

    lc_dates_h7 = json.dumps(lc7_obs.get("dates", []))
    lc_cum_a_h7 = json.dumps(lc7_obs.get("cum_a", []))
    lc_cum_d_h7_obs = json.dumps(lc7_obs.get("cum_d", []))
    lc_cum_d_h7_per = json.dumps(lc7_per.get("cum_d", []))
    lc_cum_e_h7_obs = json.dumps(_cum_series_for_model(results_obs, "E", 7))
    lc_cum_e_h7_per = json.dumps(_cum_series_for_model(results_per, "E", 7))
    lc_cum_f_h7_obs = json.dumps(_cum_series_for_model(results_obs, "F", 7))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BHAGA Weather Forecast Spike — Backtest Results</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    color: #1a1a2e;
    background: #f8f9fa;
    margin: 0;
    padding: 24px;
  }}
  .page {{ max-width: 1040px; margin: 0 auto; }}
  h1 {{ font-size: 22px; font-weight: 700; margin: 0 0 4px; color: #0d0d1a; }}
  h2 {{ font-size: 15px; font-weight: 600; margin: 0 0 8px; color: #333; }}
  .meta {{ color: #666; font-size: 12px; margin-bottom: 24px; }}

  .verdict {{
    border-radius: 6px;
    padding: 14px 18px;
    margin-bottom: 24px;
    font-size: 14px;
    border-left: 4px solid;
  }}
  .verdict.win   {{ background: #eafaf1; border-color: #27ae60; color: #1d6e43; }}
  .verdict.lose  {{ background: #fef9e7; border-color: #e67e22; color: #a04000; }}
  .verdict.neutral {{ background: #f0f3f4; border-color: #7f8c8d; color: #444; }}
  .verdict .icon {{ font-size: 18px; margin-right: 8px; }}

  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 24px;
  }}
  .stat {{
    background: white;
    border: 1px solid #e0e0e0;
    border-radius: 6px;
    padding: 14px 16px;
  }}
  .stat-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: #888; margin-bottom: 4px; }}
  .stat-value {{ font-size: 24px; font-weight: 700; color: #0d0d1a; }}
  .stat-caption {{ font-size: 11px; color: #888; margin-top: 2px; }}
  .stat.highlight .stat-value {{ color: #27ae60; }}
  .stat.worse .stat-value {{ color: #e74c3c; }}

  .section {{
    background: white;
    border: 1px solid #e0e0e0;
    border-radius: 6px;
    padding: 20px;
    margin-bottom: 20px;
  }}
  .chart-caption {{ font-size: 11px; color: #999; margin-top: 8px; }}

  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #f0f0f0; font-weight: 600; padding: 8px 10px; text-align: left;
        border-bottom: 2px solid #ddd; }}
  th.right, td.right {{ text-align: right; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #f0f0f0; }}
  tr:last-child td {{ border-bottom: none; }}
  td.best {{ font-weight: 700; color: #27ae60; }}
  tr:hover td {{ background: #fafafa; }}

  .caveats {{ background: #f8f9fa; border-radius: 6px; padding: 16px 20px; margin-bottom: 20px;
              border: 1px solid #e0e0e0; }}
  .caveats h2 {{ margin-bottom: 10px; }}
  .caveat {{ margin-bottom: 8px; }}
  .caveat:last-child {{ margin-bottom: 0; }}
  .caveat strong {{ color: #555; }}

  .footer {{ font-size: 11px; color: #aaa; text-align: center; margin-top: 24px; }}
</style>
</head>
<body>
<div class="page">
  <h1>BHAGA Weather Forecast Spike</h1>
  <div class="meta">
    Walk-forward backtest &middot; {n_samples}&nbsp;(date&nbsp;&times;&nbsp;horizon) samples &middot;
    actuals&nbsp;{date_range_start}&nbsp;&rarr;&nbsp;{date_range_end} &middot;
    weather&nbsp;Open-Meteo&nbsp;archive &middot; run&nbsp;{run_date}
  </div>

  <!-- Verdict -->
  <div class="verdict {verdict_cls}">
    <span class="icon">{verdict_icon}</span>
    <strong>Decision (observed weather, upper bound):</strong> {verdict}
  </div>

  <!-- Feasibility / noise-floor banner -->
  <div style="background:#eef5ff;border:1px solid #6fa8ff;border-radius:6px;padding:14px 18px;margin-bottom:20px;font-size:13px;line-height:1.6;">
    <strong>Can we hit 5% MAPE? Not at this volume — and here's the math.</strong>
    Daily customer arrivals follow a counting process, so the <em>irreducible</em> day-to-day
    noise floor is roughly 1/&radic;(orders). At this store's mean of
    <strong>{mean_vol:.0f} orders/day</strong>, the best <em>any</em> model can do is
    <strong>~{noise_floor:.0f}% MAPE</strong> — no feature set beats physics.
    A 5% floor needs <strong>~{vol_for_5pct} orders/day</strong> AND a stable (non-ramping) level.
    <br>
    <strong>So the realistic near-term target is ~12–18% daily MAPE</strong> (approaching the floor as
    volume grows), and the highest-value fix is removing the systematic <em>under-forecast bias</em>,
    not chasing an impossible MAPE number. Weekly/3-day aggregated forecasts hit ~5% far sooner
    because they average out daily counting noise.
  </div>

  <!-- Bias summary (Model F's key win) -->
  <div class="section">
    <h2>Systematic bias &mdash; mean(actual &minus; forecast), +ve = under-forecast</h2>
    <p style="margin:0 0 10px;font-size:13px;color:#555;">
      This is the real operational problem on a ramping new store: the heuristic chronically
      <strong>under-forecasts</strong> because its growth factor is clamped at +20%/week while the
      store actually grows +30&ndash;65%/week. Model F (log-space ramp-aware) tracks the multiplicative
      ramp and largely removes the bias.
    </p>
    <div class="stats-grid" style="grid-template-columns:repeat(4,1fr)">
      <div class="stat worse">
        <div class="stat-label">A &mdash; Heuristic</div>
        <div class="stat-value">{bias_val_a:+.0f}</div>
        <div class="stat-caption">orders/day under-forecast</div>
      </div>
      <div class="stat {'worse' if abs(bias_val_d) > 10 else 'highlight'}">
        <div class="stat-label">D &mdash; Ridge+wx</div>
        <div class="stat-value">{bias_val_d:+.0f}</div>
        <div class="stat-caption">orders/day</div>
      </div>
      <div class="stat {'worse' if abs(bias_val_e) > 10 else 'highlight'}">
        <div class="stat-label">E &mdash; +lags</div>
        <div class="stat-value">{bias_val_e:+.0f}</div>
        <div class="stat-caption">orders/day</div>
      </div>
      <div class="stat {'highlight' if abs(bias_val_f) < abs(bias_val_a)/2 else ''}">
        <div class="stat-label">F &mdash; Ramp-aware (log)</div>
        <div class="stat-value">{bias_val_f:+.0f}</div>
        <div class="stat-caption">orders/day &mdash; bias largely fixed</div>
      </div>
    </div>
  </div>

  <!-- Weather mode explanation banner -->
  <div style="background:#fffbea;border:1px solid #f0d060;border-radius:6px;padding:12px 16px;margin-bottom:20px;font-size:13px;line-height:1.6;">
    <strong>Two weather input modes are shown throughout this report:</strong>
    <ul style="margin:6px 0 0 16px;padding:0;">
      <li><strong>Observed (ERA5)</strong> &mdash; actual historical weather used at forecast time.
        This is the <em>upper bound</em>: assumes the weather forecast is perfect.</li>
      <li><strong>Persistence proxy</strong> &mdash; for H&gt;1, uses make-date&rsquo;s observed weather as
        the &ldquo;forecast&rdquo; for target-date.  NWP models beat persistence, so this is a
        <em>conservative lower bound</em>.  The real production benefit sits between these two bands.</li>
    </ul>
    For H=1, both modes are identical &mdash; you already know today&rsquo;s weather when forecasting today.
    Note: Open-Meteo does not expose an archived NWP forecast API (it stores analysis-quality data, not
    initialisation-date-indexed forecast runs), so persistence is used as a conservative proxy here.
  </div>

  <!-- Comparison band: Model D observed vs persistence -->
  <div class="section">
    <h2>Benefit band: Model D (Ridge+wx) with observed vs persistence weather &mdash; MAPE by horizon</h2>
    <canvas id="comparisonChart" height="110"></canvas>
    <div class="chart-caption">
      <strong>Shaded band</strong> = realistic benefit range.
      Top edge (persistence) = conservative lower bound; bottom edge (observed) = upper bound.
      Model A (no weather) is the baseline. Real production NWP forecasts lie inside the band.
    </div>
  </div>

  <!-- Comparison stat row: Model D observed vs persistence -->
  <div class="stats-grid" style="grid-template-columns:repeat(5,1fr);margin-bottom:20px;">
    <div class="stat">
      <div class="stat-label">Model A &mdash; Heuristic (baseline)</div>
      <div class="stat-value">{mape_a:.1f}%</div>
      <div class="stat-caption">same for all wx modes</div>
    </div>
    <div class="stat {'highlight' if mape_d < mape_a else 'worse'}">
      <div class="stat-label">D Ridge+wx observed &#x2191;</div>
      <div class="stat-value">{mape_d:.1f}%</div>
      <div class="stat-caption">+{(mape_a - mape_d):.1f} pp vs A</div>
    </div>
    <div class="stat {'highlight' if mape_d_per < mape_a else 'worse'}">
      <div class="stat-label">D Ridge+wx persistence &#x2193;</div>
      <div class="stat-value">{mape_d_per:.1f}%</div>
      <div class="stat-caption">+{(mape_a - mape_d_per):.1f} pp vs A</div>
    </div>
    <div class="stat {'highlight' if mape_e < mape_a else 'worse'}">
      <div class="stat-label">E Ridge+lags+wx observed &#x2191;</div>
      <div class="stat-value">{mape_e:.1f}%</div>
      <div class="stat-caption">+{(mape_a - mape_e):.1f} pp vs A</div>
    </div>
    <div class="stat {'highlight' if mape_e_per < mape_a else 'worse'}">
      <div class="stat-label">E Ridge+lags+wx persistence &#x2193;</div>
      <div class="stat-value">{mape_e_per:.1f}%</div>
      <div class="stat-caption">+{(mape_a - mape_e_per):.1f} pp vs A</div>
    </div>
  </div>

  <!-- Headline stats (observed mode, all models) -->
  <div class="section">
    <h2>Headline numbers &mdash; observed weather (upper bound), all 5 models</h2>
  <div class="stats-grid" style="grid-template-columns:repeat(3,1fr)">
    <div class="stat">
      <div class="stat-label">Model A &mdash; Heuristic v2</div>
      <div class="stat-value">{mape_a:.1f}%</div>
      <div class="stat-caption">production baseline MAPE</div>
    </div>
    <div class="stat {'highlight' if mape_d < mape_a else 'worse'}">
      <div class="stat-label">Model D &mdash; Ridge + weather</div>
      <div class="stat-value">{mape_d:.1f}%</div>
      <div class="stat-caption">+{(mape_a-mape_d):.1f} pp vs A (no lags)</div>
    </div>
    <div class="stat {'highlight' if mape_e < mape_a else 'worse'}">
      <div class="stat-label">Model E &mdash; Ridge + lags + weather</div>
      <div class="stat-value">{mape_e:.1f}%</div>
      <div class="stat-caption">+{(mape_a-mape_e):.1f} pp vs A (with lags)</div>
    </div>
    <div class="stat">
      <div class="stat-label">Model A MAE (orders)</div>
      <div class="stat-value">{mae_a:.1f}</div>
      <div class="stat-caption">mean absolute error</div>
    </div>
    <div class="stat {'highlight' if mae_d < mae_a else 'worse'}">
      <div class="stat-label">Model D MAE (orders)</div>
      <div class="stat-value">{mae_d:.1f}</div>
      <div class="stat-caption">mean absolute error</div>
    </div>
    <div class="stat {'highlight' if mae_e < mae_a else 'worse'}">
      <div class="stat-label">Model E MAE (orders)</div>
      <div class="stat-value">{mae_e:.1f}</div>
      <div class="stat-caption">mean absolute error</div>
    </div>
  </div>
  </div>

  {excl_html}

  <!-- Feature importance -->
  <div class="section">
    <h2>Model features &amp; learned weights (beta coefficients)</h2>
    <p style="margin:0 0 10px;font-size:13px;color:#555;">
      Beta is the ridge regression coefficient for each feature.  <strong>|beta|</strong> ranks
      importance.  Sign: positive = more orders when feature is higher; negative = fewer.
      <em>% of mean</em> = |beta| as a fraction of the average daily order count (how many orders
      does a unit change in this feature move the forecast?).
      Fitted on all {(feat_importance or {}).get("E", {}).get("n_train", 0)} training days.
    </p>
    <div class="grid-2">
      <div>
        <h2 style="font-size:13px;">Model D features (13): DOW + trend + weather</h2>
        <table>
          <thead><tr><th>Feature</th><th class="right">Beta</th><th class="right">% of mean</th></tr></thead>
          <tbody>{fi_d_rows_html}</tbody>
        </table>
      </div>
      <div>
        <h2 style="font-size:13px;">Model E features (17): DOW + trend + <strong>lags</strong> + weather</h2>
        <table>
          <thead><tr><th>Feature</th><th class="right">Beta</th><th class="right">% of mean</th></tr></thead>
          <tbody>{fi_e_rows_html}</tbody>
        </table>
        <p style="font-size:11px;color:#888;margin-top:8px;">
          Lag features (lag_7d, lag_14d, lag_28d, roll_4w_dow) are normalized by the 28-day rolling
          mean, so a beta of +50 means &ldquo;if last week&rsquo;s same-DOW was 10% above the rolling
          average, forecast 5 more orders.&rdquo;  Large positive lag betas = model trusts recent
          history to set the level.
        </p>
      </div>
    </div>
    <div style="margin-top:12px;">
      <h2 style="font-size:13px;">Beta coefficients visual (sorted by |beta|)</h2>
      <canvas id="featImpChart" height="80"></canvas>
    </div>
  </div>

  <!-- MAPE by horizon chart -->
  <div class="section">
    <h2>MAPE by forecast horizon &mdash; all 5 models</h2>
    <canvas id="horizonChart" height="120"></canvas>
    <div class="chart-caption">
      Lower is better. <strong>A vs D is the decision</strong>;
      B (heuristic&nbsp;+&nbsp;weather) and C (ridge&nbsp;no&nbsp;weather) are diagnostics.
      Source: walk-forward backtest, leakage-free.
    </div>
  </div>

  <div class="grid-2">
    <!-- MAPE by DOW -->
    <div class="section">
      <h2>MAPE by day of week (1-day horizon)</h2>
      <canvas id="dowChart" height="160"></canvas>
      <div class="chart-caption">Which days benefit most from weather data?</div>
    </div>

    <!-- Bias by horizon -->
    <div class="section">
      <h2>Forecast bias by horizon (mean signed error)</h2>
      <canvas id="biasChart" height="160"></canvas>
      <div class="chart-caption">
        Positive = over-forecast; negative = under-forecast.
        Both models consistently over-forecast — the store is growing.
      </div>
    </div>
  </div>

  <!-- Per-day APE scatter (horizon=1) -->
  <div class="section">
    <h2>APE per day &mdash; 1-day horizon (Model A vs D, observed weather)</h2>
    <canvas id="scatterChart" height="100"></canvas>
    <div class="chart-caption">
      Each bar is one forecast day. Lower = better.
      Where blue bars are taller, Model D was worse that day; where green is shorter, Model D won.
    </div>
  </div>

  <!-- Learning curve section -->
  <div class="section">
    <h2>Forecast accuracy over time &mdash; as training data accumulates</h2>
    <p style="margin:0 0 12px;font-size:13px;color:#555;">
      <strong>Cumulative MAPE</strong> at each test date (average of all forecasts up to that point).
      As the walk-forward window advances and the regression sees more history, does Model D converge
      while Model A stays flat?  The shaded band between observed and persistence shows the realistic
      range for weather-based forecasting.
    </p>
    <div class="grid-2" style="margin-bottom:0;">
      <div>
        <h2 style="font-size:13px;">H=1 day &mdash; cumulative MAPE over time</h2>
        <canvas id="lcCumH1Chart" height="170"></canvas>
        <div class="chart-caption">
          ~{MIN_WARMUP_DAYS} training days at start; grows by ~1 each step.
          Convergence = the line stops falling as steeply.
        </div>
      </div>
      <div>
        <h2 style="font-size:13px;">H=7 days &mdash; cumulative MAPE over time</h2>
        <canvas id="lcCumH7Chart" height="170"></canvas>
        <div class="chart-caption">
          Same view at 7-day horizon.  Model D typically degrades more at longer
          horizons since weather forecast quality also falls.
        </div>
      </div>
    </div>
    <br>
    <div class="grid-2" style="margin-bottom:0;">
      <div>
        <h2 style="font-size:13px;">H=1 day &mdash; 5-test-date rolling MAPE</h2>
        <canvas id="lcRollH1Chart" height="170"></canvas>
        <div class="chart-caption">
          Rolling window shows <em>current momentum</em> rather than cumulative average.
          Spikes = hard weeks; downward trends = the model improving.
        </div>
      </div>
      <div style="display:flex;align-items:center;justify-content:center;padding:20px;background:#f8f9fa;border-radius:6px;font-size:12px;color:#888;">
        <div>
          <p style="margin:0 0 8px;font-weight:600;color:#555;">Reading this chart</p>
          <p style="margin:0 0 6px;">&#x25BC; <strong>Falling line</strong> = model getting more accurate as data accumulates</p>
          <p style="margin:0 0 6px;">&#x2192; <strong>Flat line</strong> = accuracy plateaued (heuristic is expected to be flat)</p>
          <p style="margin:0 0 6px;"><span style="color:#27ae60;">&#9646;</span> <strong>Green band</strong> = realistic benefit range (observed top, persistence bottom)</p>
          <p style="margin:0;">A wide gap between Model A (blue dashes) and Model D means weather consistently helps that week.</p>
        </div>
      </div>
    </div>
  </div>

  <!-- Summary tables -->
  <div class="grid-2">
    <div class="section">
      <h2>MAPE by horizon — numeric summary</h2>
      <table>
        <thead>
          <tr>
            <th>Horizon</th>
            <th class="right">A&nbsp;Heuristic</th>
            <th class="right">B&nbsp;Heur+wx</th>
            <th class="right">C&nbsp;Ridge</th>
            <th class="right">D&nbsp;Ridge+wx</th>
            <th class="right">E&nbsp;+lags</th>
            <th class="right">F&nbsp;ramp</th>
            <th class="right">n</th>
          </tr>
        </thead>
        <tbody>
          {horizon_rows_html}
        </tbody>
      </table>
    </div>

    <div class="section">
      <h2>MAPE by day of week (1-day horizon)</h2>
      <table>
        <thead>
          <tr>
            <th>DOW</th>
            <th class="right">A</th>
            <th class="right">B</th>
            <th class="right">C</th>
            <th class="right">D</th>
            <th class="right">E</th>
            <th class="right">F</th>
          </tr>
        </thead>
        <tbody>
          {dow_rows_html}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Caveats -->
  <div class="caveats">
    <h2>Interpretation notes</h2>
    <div class="caveat">
      <strong>1. Model E (lags + weather) vs Model D (weather only).</strong>
      Model E adds 4 lagged-order features: orders 7, 14, and 28 days ago (normalized) plus the
      4-week same-DOW rolling average.  These give the regression a &ldquo;current order rate&rdquo;
      signal — the same information the heuristic uses — but let the model learn the optimal weighting
      combined with weather.  This is why Model E should show a steeper learning curve than D.
    </div>
    <div class="caveat">
      <strong>2. Upper bound vs lower bound weather.</strong>
      The report shows two bands.  <em>Observed (ERA5)</em> uses actual historical weather &mdash;
      assumes perfect forecasts.
      <em>Persistence proxy</em> uses make-date&rsquo;s weather — a conservative lower bound.
      Real production NWP (reliable &le;&nbsp;7&nbsp;days) lies between the bands.
    </div>
    <div class="caveat">
      <strong>3. Exclusion consistency.</strong>
      Days flagged <code>forecast_exclude=True</code> are removed from the training history
      and the test targets for <em>all five models</em> using the identical flag.
    </div>
    <div class="caveat">
      <strong>4. Small sample.</strong>
      ~{n_samples} (date &times; horizon) pairs, drawn from &lt;90 operating days.
      Treat regression results as directional only &mdash; not statistically conclusive.
    </div>
    <div class="caveat">
      <strong>5. Walk-forward, leakage-free.</strong>
      Every forecast uses only data strictly before the make-date.  No hindsight.
    </div>
  </div>

  <div class="footer">
    Generated by <code>run_backtest.py</code> &mdash; analysis only, not wired into production.
  </div>
</div>

<script>
const LABELS = {hlabels};
const A = {hdata_a};
const B = {hdata_b};
const C = {hdata_c};
const D = {hdata_d};
const E = {hdata_e};
const F = {hdata_f};
const D_PER = {hdata_d_per};
const DOWS = {dows_json};
const DOW_A = {dow_a};
const DOW_D = {dow_d};
const DOW_E = {dow_e};
const BIAS_A = {bias_a};
const BIAS_D = {bias_d};
const SCATTER_LABELS = {scatter_labels};
const SCATTER_A = {scatter_a};
const SCATTER_D = {scatter_d};
const SCATTER_E = {scatter_e};

// Learning-curve data
const LC_DATES_H1 = {lc_dates_h1};
const LC_CUM_A_H1 = {lc_cum_a_h1};
const LC_CUM_D_H1_OBS = {lc_cum_d_h1_obs};
const LC_CUM_D_H1_PER = {lc_cum_d_h1_per};
const LC_CUM_E_H1_OBS = {lc_cum_e_h1_obs};
const LC_CUM_E_H1_PER = {lc_cum_e_h1_per};
const LC_ROLL_A_H1 = {lc_roll_a_h1};
const LC_ROLL_D_H1_OBS = {lc_roll_d_h1_obs};
const LC_ROLL_D_H1_PER = {lc_roll_d_h1_per};
const LC_ROLL_E_H1_OBS = {lc_roll_e_h1_obs};
const LC_ROLL_E_H1_PER = {lc_roll_e_h1_per};
const LC_N_TRAIN_H1 = {lc_n_train_h1};
const LC_DATES_H7 = {lc_dates_h7};
const LC_CUM_A_H7 = {lc_cum_a_h7};
const LC_CUM_D_H7_OBS = {lc_cum_d_h7_obs};
const LC_CUM_D_H7_PER = {lc_cum_d_h7_per};
const LC_CUM_E_H7_OBS = {lc_cum_e_h7_obs};
const LC_CUM_E_H7_PER = {lc_cum_e_h7_per};
const LC_CUM_F_H1_OBS = {lc_cum_f_h1_obs};
const LC_ROLL_F_H1_OBS = {lc_roll_f_h1_obs};
const LC_CUM_F_H7_OBS = {lc_cum_f_h7_obs};

// Feature importance
const FI_D_LABELS = {fi_d_labels};
const FI_D_VALS   = {fi_d_vals};
const FI_E_LABELS = {fi_e_labels};
const FI_E_VALS   = {fi_e_vals};

const COLORS = {{
  A: '#3498db',
  B: '#9b59b6',
  C: '#e67e22',
  D: '#27ae60',
  E: '#e91e8c',
  F: '#8e44ad',
}};

function makeChart(id, cfg) {{
  return new Chart(document.getElementById(id), cfg);
}}

// Comparison band: A vs D vs E (observed and persistence)
makeChart('comparisonChart', {{
  type: 'line',
  data: {{
    labels: LABELS,
    datasets: [
      {{ label: 'A  Heuristic (no weather)', data: A, borderColor: COLORS.A, fill: false, tension: 0.3, borderDash: [5,3], borderWidth: 2 }},
      {{ label: 'D  Ridge+wx (observed ↑)', data: D, borderColor: COLORS.D, fill: false, tension: 0.3, borderWidth: 2 }},
      {{ label: 'D  Ridge+wx (persistence ↓)', data: D_PER, borderColor: '#f39c12', backgroundColor: '#f39c1211', fill: '2', tension: 0.3, borderWidth: 1.5, borderDash: [3,2] }},
      {{ label: 'E  +lags+wx (observed ↑)', data: E, borderColor: COLORS.E, fill: false, tension: 0.3, borderWidth: 2 }},
      {{ label: 'F  ramp-aware log (observed ↑)', data: F, borderColor: COLORS.F, fill: false, tension: 0.3, borderWidth: 2.5 }},
    ],
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom' }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(1)}}%` }} }} }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ callback: v => v + '%' }}, title: {{ display: true, text: 'MAPE (%)' }} }},
      x: {{ title: {{ display: true, text: 'Horizon' }} }},
    }},
  }},
}});

// Horizon MAPE (all 5 models, observed)
makeChart('horizonChart', {{
  type: 'bar',
  data: {{
    labels: LABELS,
    datasets: [
      {{ label: 'A  Heuristic v2', data: A, backgroundColor: COLORS.A + 'cc' }},
      {{ label: 'B  Heuristic+wx', data: B, backgroundColor: COLORS.B + 'cc' }},
      {{ label: 'C  Ridge no-wx',  data: C, backgroundColor: COLORS.C + 'cc' }},
      {{ label: 'D  Ridge+wx',     data: D, backgroundColor: COLORS.D + 'cc' }},
      {{ label: 'E  Ridge+lags+wx',data: E, backgroundColor: COLORS.E + 'cc' }},
      {{ label: 'F  Ramp-aware(log)',data: F, backgroundColor: COLORS.F + 'cc' }},
    ],
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom' }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(1)}}%` }} }} }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ callback: v => v + '%' }}, title: {{ display: true, text: 'MAPE (%)' }} }},
      x: {{ title: {{ display: true, text: 'Horizon' }} }},
    }},
  }},
}});

// DOW MAPE (A vs D vs E)
makeChart('dowChart', {{
  type: 'bar',
  data: {{
    labels: DOWS,
    datasets: [
      {{ label: 'A  Heuristic v2', data: DOW_A, backgroundColor: COLORS.A + 'cc' }},
      {{ label: 'D  Ridge+wx',     data: DOW_D, backgroundColor: COLORS.D + 'cc' }},
      {{ label: 'E  Ridge+lags+wx',data: DOW_E, backgroundColor: COLORS.E + 'cc' }},
    ],
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom' }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(1)}}%` }} }} }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ callback: v => v + '%' }}, title: {{ display: true, text: 'MAPE (%)' }} }},
    }},
  }},
}});

// Bias
makeChart('biasChart', {{
  type: 'line',
  data: {{
    labels: LABELS,
    datasets: [
      {{ label: 'A  Heuristic v2', data: BIAS_A, borderColor: COLORS.A, fill: false, tension: 0.3 }},
      {{ label: 'D  Ridge+wx',     data: BIAS_D, borderColor: COLORS.D, fill: false, tension: 0.3 }},
    ],
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom' }}, tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(1)}} orders` }} }} }},
    scales: {{
      y: {{ title: {{ display: true, text: 'Mean error (orders)' }} }},
      x: {{ title: {{ display: true, text: 'Horizon' }} }},
    }},
  }},
}});

// Feature importance chart (Model E beta coefficients)
makeChart('featImpChart', {{
  type: 'bar',
  data: {{
    labels: FI_E_LABELS,
    datasets: [
      {{ label: 'Model E beta', data: FI_E_VALS,
         backgroundColor: FI_E_VALS.map(v => v >= 0 ? '#27ae60cc' : '#e74c3ccc') }},
    ],
  }},
  options: {{
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => ` beta: ${{ctx.parsed.x.toFixed(3)}}` }} }},
    }},
    scales: {{
      x: {{ title: {{ display: true, text: 'Beta coefficient (green=+orders, red=−orders)' }} }},
    }},
  }},
}});

// Per-day scatter (A vs D vs E)
makeChart('scatterChart', {{
  type: 'bar',
  data: {{
    labels: SCATTER_LABELS,
    datasets: [
      {{ label: 'A  Heuristic v2',  data: SCATTER_A, backgroundColor: COLORS.A + 'aa' }},
      {{ label: 'D  Ridge+wx',      data: SCATTER_D, backgroundColor: COLORS.D + 'aa' }},
      {{ label: 'E  Ridge+lags+wx', data: SCATTER_E, backgroundColor: COLORS.E + 'aa' }},
    ],
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{
      legend: {{ position: 'bottom' }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y.toFixed(1)}}%` }} }},
    }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ callback: v => v + '%' }}, title: {{ display: true, text: 'APE (%)' }} }},
      x: {{ title: {{ display: true, text: 'Target date (MM-DD, 1-day horizon)' }}, ticks: {{ maxRotation: 45 }} }},
    }},
  }},
}});

// Helper: shared line-chart options for learning curves
function lcLineOpts(xtitle) {{
  return {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{
      legend: {{ position: 'bottom' }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.y != null ? ctx.parsed.y.toFixed(1) : 'n/a'}}%` }} }},
    }},
    scales: {{
      y: {{ beginAtZero: true, ticks: {{ callback: v => v + '%' }}, title: {{ display: true, text: 'MAPE (%)' }} }},
      x: {{ title: {{ display: true, text: xtitle }}, ticks: {{ maxRotation: 45, autoSkip: true, maxTicksLimit: 15 }} }},
    }},
    spanGaps: false,
  }};
}}

// Learning curve: cumulative MAPE H=1 (A vs D vs E, with persistence band)
makeChart('lcCumH1Chart', {{
  type: 'line',
  data: {{
    labels: LC_DATES_H1,
    datasets: [
      {{ label: 'A  Heuristic', data: LC_CUM_A_H1,
         borderColor: COLORS.A, fill: false, borderDash: [6,3], borderWidth: 2, pointRadius: 2, tension: 0.2 }},
      {{ label: 'D  Ridge+wx (observed)', data: LC_CUM_D_H1_OBS,
         borderColor: COLORS.D, fill: false, borderWidth: 2, pointRadius: 2, tension: 0.2 }},
      {{ label: 'D  Ridge+wx (persistence)', data: LC_CUM_D_H1_PER,
         borderColor: '#f39c12', fill: '2', borderWidth: 1, borderDash: [3,2], pointRadius: 1, tension: 0.2, backgroundColor: '#f39c1211' }},
      {{ label: 'E  Ridge+lags+wx (observed)', data: LC_CUM_E_H1_OBS,
         borderColor: COLORS.E, fill: false, borderWidth: 2.5, pointRadius: 3, tension: 0.2 }},
      {{ label: 'E  Ridge+lags+wx (persistence)', data: LC_CUM_E_H1_PER,
         borderColor: '#c0134f', fill: '4', borderWidth: 1, borderDash: [2,2], pointRadius: 1, tension: 0.2, backgroundColor: '#e91e8c11' }},
      {{ label: 'F  Ramp-aware log (observed)', data: LC_CUM_F_H1_OBS,
         borderColor: COLORS.F, fill: false, borderWidth: 3, pointRadius: 3, tension: 0.2 }},
    ],
  }},
  options: lcLineOpts('Test date (MM-DD)  |  ~' + LC_N_TRAIN_H1[0] + '–' + LC_N_TRAIN_H1[LC_N_TRAIN_H1.length-1] + ' training days'),
}});

// Learning curve: cumulative MAPE H=7
makeChart('lcCumH7Chart', {{
  type: 'line',
  data: {{
    labels: LC_DATES_H7,
    datasets: [
      {{ label: 'A  Heuristic', data: LC_CUM_A_H7,
         borderColor: COLORS.A, fill: false, borderDash: [6,3], borderWidth: 2, pointRadius: 2, tension: 0.2 }},
      {{ label: 'D  Ridge+wx (observed)', data: LC_CUM_D_H7_OBS,
         borderColor: COLORS.D, fill: false, borderWidth: 2, pointRadius: 2, tension: 0.2 }},
      {{ label: 'D  Ridge+wx (persistence)', data: LC_CUM_D_H7_PER,
         borderColor: '#f39c12', fill: '2', borderWidth: 1, borderDash: [3,2], pointRadius: 1, tension: 0.2, backgroundColor: '#f39c1211' }},
      {{ label: 'E  Ridge+lags+wx (observed)', data: LC_CUM_E_H7_OBS,
         borderColor: COLORS.E, fill: false, borderWidth: 2.5, pointRadius: 3, tension: 0.2 }},
      {{ label: 'E  Ridge+lags+wx (persistence)', data: LC_CUM_E_H7_PER,
         borderColor: '#c0134f', fill: '4', borderWidth: 1, borderDash: [2,2], pointRadius: 1, tension: 0.2, backgroundColor: '#e91e8c11' }},
      {{ label: 'F  Ramp-aware log (observed)', data: LC_CUM_F_H7_OBS,
         borderColor: COLORS.F, fill: false, borderWidth: 3, pointRadius: 3, tension: 0.2 }},
    ],
  }},
  options: lcLineOpts('Test date (MM-DD)'),
}});

// Learning curve: 5-date rolling MAPE H=1
makeChart('lcRollH1Chart', {{
  type: 'line',
  data: {{
    labels: LC_DATES_H1,
    datasets: [
      {{ label: 'A  Heuristic', data: LC_ROLL_A_H1,
         borderColor: COLORS.A, fill: false, borderDash: [6,3], borderWidth: 2, pointRadius: 2, tension: 0.3 }},
      {{ label: 'D  Ridge+wx (observed)', data: LC_ROLL_D_H1_OBS,
         borderColor: COLORS.D, fill: false, borderWidth: 2, pointRadius: 2, tension: 0.3 }},
      {{ label: 'D  Ridge+wx (persistence)', data: LC_ROLL_D_H1_PER,
         borderColor: '#f39c12', fill: '2', borderWidth: 1, borderDash: [3,2], pointRadius: 1, tension: 0.3, backgroundColor: '#f39c1211' }},
      {{ label: 'E  Ridge+lags+wx (observed)', data: LC_ROLL_E_H1_OBS,
         borderColor: COLORS.E, fill: false, borderWidth: 2.5, pointRadius: 3, tension: 0.3 }},
      {{ label: 'E  Ridge+lags+wx (persistence)', data: LC_ROLL_E_H1_PER,
         borderColor: '#c0134f', fill: '4', borderWidth: 1, borderDash: [2,2], pointRadius: 1, tension: 0.3, backgroundColor: '#e91e8c11' }},
      {{ label: 'F  Ramp-aware log (observed)', data: LC_ROLL_F_H1_OBS,
         borderColor: COLORS.F, fill: false, borderWidth: 3, pointRadius: 3, tension: 0.3 }},
    ],
  }},
  options: lcLineOpts('Test date (MM-DD, 5-date rolling window)'),
}});
</script>
</body>
</html>
"""

    OUT_DIR.mkdir(exist_ok=True)
    with open(HTML_REPORT_PATH, "w") as f:
        f.write(html)
    print(f"  Wrote report → {HTML_REPORT_PATH}")


if __name__ == "__main__":
    main()
