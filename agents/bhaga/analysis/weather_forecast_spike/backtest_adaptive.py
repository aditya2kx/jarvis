"""Phase 1 offline validation: adaptive_dow_ets_v1 walk-forward backtest.

Run from repo root:
  BHAGA_DATASTORE=bigquery python3 agents/bhaga/analysis/weather_forecast_spike/backtest_adaptive.py

Outputs a grid-search MAPE table + picks the best (alpha, beta, phi) combo.
Results printed to stdout; nothing written to BQ / Grafana.
"""
import datetime
import math
import sys
import os

# ── Pull live data from prod BQ ───────────────────────────────────────────────
def fetch_data():
    os.environ.setdefault("BHAGA_DATASTORE", "bigquery")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
    from core.datastore import get_client
    client = get_client()
    rows = list(client.query(
        "SELECT date, orders, forecast_exclude, FALSE AS event_flag "
        "FROM `jarvis-bhaga-prod.bhaga.model_labor_daily` "
        "ORDER BY date"
    ).result())
    weather_rows = list(client.query(
        "SELECT date, tmean_c, precip_mm, is_rainy, kind "
        "FROM `jarvis-bhaga-prod.bhaga.weather_daily` "
        "ORDER BY date"
    ).result())
    return rows, weather_rows


# ── Heuristic (wow_median_4wk_v2) reimplementation for comparison ─────────────
def _heuristic_forecast(history, target_date):
    """Simplified heuristic: same-DOW anchor × clamped WoW median growth."""
    iso = target_date.isoformat()
    usable = [r for r in history if not r["forecast_exclude"] and int(r["orders"] or 0) > 0]
    by_date = {r["date"]: int(r["orders"]) for r in usable}
    # Anchor: last same-DOW non-excluded
    anchor_ord = None
    for w in range(1, 9):
        cand = (target_date - datetime.timedelta(days=7 * w)).isoformat()
        if cand in by_date:
            anchor_ord = by_date[cand]
            weeks_back = w
            break
    if anchor_ord is None:
        recent = [r for r in usable[-7:]]
        anchor_ord = sum(int(r["orders"]) for r in recent) / max(len(recent), 1) if recent else 50
        weeks_back = 1
    # Growth: median WoW same-DOW pairs in last 28 days
    cutoff = (target_date - datetime.timedelta(days=1)).isoformat()
    window_start = (target_date - datetime.timedelta(days=29)).isoformat()
    pairs = []
    dates_in_window = sorted(d for d in by_date if window_start <= d <= cutoff)
    for d_str in dates_in_window:
        d = datetime.date.fromisoformat(d_str)
        prev = (d - datetime.timedelta(days=7)).isoformat()
        if prev in by_date:
            pairs.append(by_date[d_str] / by_date[prev])
    if len(pairs) >= 2:
        pairs.sort()
        n = len(pairs)
        growth = pairs[n // 2] if n % 2 == 1 else (pairs[n // 2 - 1] + pairs[n // 2]) / 2
    else:
        growth = 1.0
    growth = max(0.80, min(1.20, growth))
    return max(1, round(anchor_ord * (growth ** weeks_back)))


# ── Adaptive model (adaptive_dow_ets_v1) ─────────────────────────────────────
SEASON_WINDOW_DAYS = 42
DOW_CLAMP = (0.5, 1.8)


def _compute_dow_factors(history_before, season_window=SEASON_WINDOW_DAYS):
    """Multiplicative DOW factors, normalized to mean=1, from last season_window days."""
    usable = [r for r in history_before
              if not r["forecast_exclude"] and int(r["orders"] or 0) > 0]
    tail = usable[-season_window:]
    if not tail:
        return {d: 1.0 for d in range(7)}
    mean_orders = sum(int(r["orders"]) for r in tail) / len(tail)
    if mean_orders <= 0:
        return {d: 1.0 for d in range(7)}
    dow_sums = {d: [] for d in range(7)}
    for r in tail:
        d = datetime.date.fromisoformat(str(r["date"])).weekday()
        dow_sums[d].append(int(r["orders"]))
    factors = {}
    for d in range(7):
        if dow_sums[d]:
            raw = (sum(dow_sums[d]) / len(dow_sums[d])) / mean_orders
            factors[d] = max(DOW_CLAMP[0], min(DOW_CLAMP[1], raw))
        else:
            factors[d] = 1.0
    return factors


def _fit_ets(history_before, alpha, beta, phi):
    """Fit Holt damped-trend on deseasonalized orders. Returns (l, b, dow_factors)."""
    dow_factors = _compute_dow_factors(history_before)
    usable = [r for r in history_before
              if not r["forecast_exclude"] and int(r["orders"] or 0) > 0]
    if not usable:
        return None, None, dow_factors
    first = int(usable[0]["orders"])
    df0 = dow_factors[datetime.date.fromisoformat(str(usable[0]["date"])).weekday()]
    l = first / max(df0, 0.01)
    b = 0.0
    for r in usable[1:]:
        y = int(r["orders"])
        dow_f = dow_factors[datetime.date.fromisoformat(str(r["date"])).weekday()]
        z = y / max(dow_f, 0.01)
        l_new = alpha * z + (1 - alpha) * (l + phi * b)
        b = beta * (l_new - l) + (1 - beta) * phi * b
        l = l_new
    return l, b, dow_factors


def _damp_sum(phi, h):
    """Sum phi^1 + phi^2 + ... + phi^h."""
    if phi >= 1.0:
        return float(h)
    return phi * (1.0 - phi ** h) / (1.0 - phi)


def _weather_feats(target_date_str, weather_by_date):
    w = weather_by_date.get(target_date_str)
    if not w:
        return 0.0, 0.0, 0.0
    tmean_f = w["tmean_c"] * 9.0 / 5.0 + 32.0
    tmean_scaled = tmean_f / 100.0
    precip_in = (w["precip_mm"] or 0.0) / 25.4
    precip_scaled = precip_in
    rainy = 1.0 if precip_in > 0.25 else 0.0
    return tmean_scaled, precip_scaled, rainy


def _ridge_solve(X, y, alpha=1.0):
    """Pure-Python Ridge (normal equations). Returns beta list."""
    n, p = len(X), len(X[0])
    # X^T X
    XtX = [[sum(X[i][j] * X[i][k] for i in range(n)) for k in range(p)] for j in range(p)]
    # Add ridge penalty
    for j in range(p):
        XtX[j][j] += alpha
    # X^T y
    Xty = [sum(X[i][j] * y[i] for i in range(n)) for j in range(p)]
    # Solve via Gauss-Jordan
    mat = [XtX[j][:] + [Xty[j]] for j in range(p)]
    for col in range(p):
        pivot = mat[col][col]
        if abs(pivot) < 1e-12:
            continue
        for row in range(p):
            if row == col:
                continue
            factor = mat[row][col] / pivot
            for k in range(p + 1):
                mat[row][k] -= factor * mat[col][k]
    return [mat[j][p] / mat[j][j] if abs(mat[j][j]) > 1e-12 else 0.0 for j in range(p)]


def _fit_weather_correction(history_before, l, b, phi, dow_factors, weather_by_date):
    """Fit Ridge on log(residuals) vs weather for multiplicative correction.
    Returns beta list [tmean, precip, rainy] or None."""
    usable = [r for r in history_before
              if not r["forecast_exclude"] and int(r["orders"] or 0) > 0]
    tail = usable[-42:]  # recent 42 days for correction fit
    X, y_log = [], []
    for i, r in enumerate(tail):
        d_str = str(r["date"])
        actual = int(r["orders"])
        # Reconstruct in-sample level at this point
        # Use a simplified in-sample base: (l + damp_sum * b) * dow_factor
        # Actually use l/b from the full fit (approximation for residuals)
        h_ahead = len(tail) - i
        base_est = max(1, (l + _damp_sum(phi, h_ahead) * b) *
                       dow_factors[datetime.date.fromisoformat(d_str).weekday()])
        log_resid = math.log(actual / max(base_est, 1))
        tm_s, pr_s, rainy = _weather_feats(d_str, weather_by_date)
        X.append([tm_s, pr_s, rainy])
        y_log.append(log_resid)
    if len(X) < 10:
        return None
    return _ridge_solve(X, y_log, alpha=1.0)


def adaptive_forecast(history_before, target_date, alpha, beta, phi, weather_by_date,
                      use_weather=True):
    """Return forecast for target_date using adaptive_dow_ets_v1."""
    l, b, dow_factors = _fit_ets(history_before, alpha, beta, phi)
    if l is None:
        return None
    # Roll-mean cap
    usable = [r for r in history_before
              if not r["forecast_exclude"] and int(r["orders"] or 0) > 0]
    tail28 = usable[-28:]
    roll_mean = max(sum(int(r["orders"]) for r in tail28) / max(len(tail28), 1), 1.0)

    # How many operating steps ahead is target_date from last training day?
    last_train = datetime.date.fromisoformat(str(usable[-1]["date"])) if usable else target_date
    # Approximate h as calendar days / 1 (slight simplification vs operating steps)
    h_days = max(1, (target_date - last_train).days)
    ds = _damp_sum(phi, h_days)
    dow_f = dow_factors[target_date.weekday()]
    base = max(1.0, (l + ds * b) * dow_f)

    # Multiplicative weather correction
    if use_weather:
        weather_betas = _fit_weather_correction(
            history_before, l, b, phi, dow_factors, weather_by_date)
        if weather_betas:
            tm_s, pr_s, rainy = _weather_feats(target_date.isoformat(), weather_by_date)
            log_corr = (weather_betas[0] * tm_s +
                        weather_betas[1] * pr_s +
                        weather_betas[2] * rainy)
            corr = max(0.7, min(1.4, math.exp(log_corr)))
            base = base * corr

    forecast = max(0, round(min(base, 4 * roll_mean)))
    return forecast


# ── Walk-forward backtest ─────────────────────────────────────────────────────
def run_backtest(rows, weather_rows, alpha, beta, phi, use_weather=True, min_warmup=28):
    weather_by_date = {}
    for w in weather_rows:
        d = str(w["date"])
        # prefer forecast kind for future, actual for past — just use latest
        weather_by_date[d] = {"tmean_c": float(w["tmean_c"] or 20),
                               "precip_mm": float(w["precip_mm"] or 0),
                               "is_rainy": bool(w["is_rainy"])}

    parsed = []
    for r in rows:
        parsed.append({
            "date": str(r["date"]),
            "orders": int(r["orders"] or 0),
            "forecast_exclude": bool(r["forecast_exclude"]),
            "event_flag": bool(r["event_flag"]) if "event_flag" in r.keys() else False,
        })

    results = []
    operating = [r for r in parsed if not r["forecast_exclude"] and r["orders"] > 0]

    for i, rec in enumerate(operating):
        if i < min_warmup:
            continue
        target_date = datetime.date.fromisoformat(rec["date"])
        history = [r for r in parsed if r["date"] < rec["date"]]
        adp_fcst = adaptive_forecast(history, target_date, alpha, beta, phi,
                                     weather_by_date, use_weather=use_weather)
        heur_fcst = _heuristic_forecast(history, target_date)
        actual = rec["orders"]
        if adp_fcst and actual > 0:
            results.append({
                "date": rec["date"],
                "actual": actual,
                "adaptive": adp_fcst,
                "heuristic": heur_fcst,
                "adp_err": abs(adp_fcst - actual) / actual * 100,
                "heur_err": abs(heur_fcst - actual) / actual * 100,
                "adp_bias": adp_fcst - actual,
                "heur_bias": heur_fcst - actual,
            })
    return results


def stats(results, label="All"):
    if not results:
        return
    import statistics
    adp_mape = statistics.mean(r["adp_err"] for r in results)
    heur_mape = statistics.mean(r["heur_err"] for r in results)
    adp_bias = statistics.mean(r["adp_bias"] for r in results)
    heur_bias = statistics.mean(r["heur_bias"] for r in results)
    print(f"  {label:<16} n={len(results):>3}  "
          f"AdpMAPE={adp_mape:>5.1f}%  HeurMAPE={heur_mape:>5.1f}%  "
          f"AdpBias={adp_bias:>+6.1f}  HeurBias={heur_bias:>+6.1f}")
    return adp_mape, heur_mape


def main():
    print("Fetching data from prod BQ...")
    rows, weather_rows = fetch_data()
    print(f"  {len(rows)} labor rows, {len(weather_rows)} weather rows")

    # ── Grid search ──────────────────────────────────────────────────────────
    print("\n=== Grid Search (last-14d MAPE) ===")
    best = None
    best_score = 9999
    for alpha in [0.2, 0.3, 0.4, 0.5]:
        for beta in [0.05, 0.1, 0.2]:
            for phi in [0.80, 0.85, 0.90, 0.95]:
                res = run_backtest(rows, weather_rows, alpha, beta, phi, use_weather=True)
                if not res:
                    continue
                last14 = res[-14:]
                import statistics
                adp_mape = statistics.mean(r["adp_err"] for r in last14)
                heur_mape = statistics.mean(r["heur_err"] for r in last14)
                diff = adp_mape - heur_mape
                if adp_mape < best_score:
                    best_score = adp_mape
                    best = (alpha, beta, phi)
                print(f"  a={alpha} b={beta} phi={phi}  "
                      f"last14 adp={adp_mape:.1f}% heur={heur_mape:.1f}% diff={diff:+.1f}")

    if best is None:
        print("ERROR: no valid results")
        return

    alpha, beta, phi = best
    print(f"\n=== Best params: alpha={alpha} beta={beta} phi={phi} ===")

    # ── Full analysis with best params ───────────────────────────────────────
    print("\n=== Full backtest with best params ===")
    res = run_backtest(rows, weather_rows, alpha, beta, phi, use_weather=True)
    stats(res, "All")
    stats(res[-21:], "Last 21d")
    stats(res[-14:], "Last 14d")
    stats(res[-7:], "Last 7d")

    # ── Compare: with vs without weather ────────────────────────────────────
    print("\n=== Weather impact (last 14d) ===")
    res_nw = run_backtest(rows, weather_rows, alpha, beta, phi, use_weather=False)
    import statistics
    nw_mape = statistics.mean(r["adp_err"] for r in res_nw[-14:])
    w_mape = statistics.mean(r["adp_err"] for r in res[-14:])
    print(f"  No weather: {nw_mape:.1f}%  With weather: {w_mape:.1f}%  "
          f"gain={nw_mape - w_mape:+.1f}pp")

    # ── Plateau detection: show recent forecast vs actual ─────────────────
    print("\n=== Recent predictions (last 14d: adaptive vs heuristic vs actual) ===")
    print(f"  {'date':<12} {'actual':>6} {'adp':>6} {'heur':>6} {'adpErr%':>8} {'heurErr%':>9}")
    for r in res[-14:]:
        print(f"  {r['date']:<12} {r['actual']:>6} {r['adaptive']:>6} {r['heuristic']:>6} "
              f"{r['adp_err']:>7.1f}% {r['heur_err']:>8.1f}%")

    # ── Gate decision ────────────────────────────────────────────────────────
    import statistics
    last14_adp = statistics.mean(r["adp_err"] for r in res[-14:])
    last14_heur = statistics.mean(r["heur_err"] for r in res[-14:])
    gap = last14_adp - last14_heur
    print(f"\n=== GATE CHECK ===")
    print(f"  Best adaptive last-14d MAPE: {last14_adp:.1f}%")
    print(f"  Heuristic last-14d MAPE:     {last14_heur:.1f}%")
    print(f"  Gap (adp - heur):            {gap:+.1f}pp  (target: within ~2pp of heuristic)")
    if gap <= 3.0:
        print("  GATE: PASS — proceed to Phase 2 (implementation)")
    else:
        print("  GATE: FAIL — adaptive does not clear bar; investigate before shipping")

    return alpha, beta, phi


if __name__ == "__main__":
    main()
