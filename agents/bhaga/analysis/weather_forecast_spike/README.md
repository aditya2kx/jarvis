# Weather Forecast Spike — Research for Posterity

> **This folder is research-only. It contains the analysis spike that motivated the Section 7A
> ramp-aware forecast implementation (`feat/ramp-forecast-7a`, 2026-06-18). Nothing here is wired
> into the production pipeline.** The spike's findings, model logic, and feature choices are
> documented in the PR description and cited in `RUNBOOK.md §15A` and `agents/bhaga/scripts/README.md`.

**Purpose:** A self-contained analysis spike that measures whether incorporating weather data (via a
regression model) improves BHAGA order-forecast accuracy versus the current `wow_median_4wk_v2`
heuristic. The deliverable is two headline accuracy numbers — current heuristic vs. weather +
regression — plus supporting diagnostics.

**Key findings (motivating Section 7A):**
- The heuristic's growth clamp [0.80, 1.20] causes systematic under-forecast bias during the store's
  hypergrowth phase (backtest bias: -19.5 orders at H=1).
- Model F (log-space Ridge + `weeks_since_open` ramp term + lags + weather) reduces bias to -5.1
  orders and improves MAPE ~10pt at H=1 under the persistence weather proxy.
- Weather adds a real ~5pt near-horizon MAPE gain even under the conservative persistence proxy.
- The Poisson noise floor at >100 orders/day is ~10% MAPE — this is the realistic target.
- See `out/report.html` for full charts and model comparisons.

---

## How to re-run

```bash
# from repo root, with .venv active
cd agents/bhaga/analysis/weather_forecast_spike

# 1. Pull actuals from BigQuery (authenticated bq CLI required)
python pull_actuals.py

# 2. Fetch observed historical weather from Open-Meteo (free, no API key)
#    → data/weather_daily.csv
python fetch_weather.py

# 3. (Optional) Fetch forecast-era weather proxy for backtesting realism
#    Uses persistence forecast (conservative lower bound) for H > 1.
#    Note: Open-Meteo does not expose initialisation-date-indexed NWP archives;
#    the persistence proxy is used as a principled lower-bound on weather benefit.
#    → data/weather_forecast.csv (make_date × target_date index)
python fetch_forecast_weather.py

# 4. Run all 4 models in both weather modes (observed + persistence) and produce
#    output CSVs, console summary, and HTML report
python run_backtest.py

# 5. Open the report in a browser
open out/report.html
```

---

## Models compared

| Model | Description | Role |
|-------|-------------|------|
| **A - Heuristic v2** | Current production formula: `anchor × growth^weeks` | Baseline (headline #1) |
| **D - Weather + Regression** | Ridge regression on DOW + trend + weather features | Candidate improvement (headline #2) |
| B - Heuristic + weather | Model A with anchor de-weathering | Diagnostic |
| C - Regression, no weather | Ridge on calendar features only | Diagnostic |

**A vs D is the decision.** B and C explain why D does or doesn't win.

---

## Caveats

1. **Two weather modes — observed (upper bound) and persistence (lower bound):**
   The backtest runs twice. *Observed* uses actual ERA5 historical weather — the best-case benefit.
   *Persistence* uses make-date's weather as the forecast for target-date — a conservative lower
   bound (NWP beats persistence for H ≤ 7 days). The realistic production benefit sits between the
   two bands. For H = 1 both modes are identical (you already know today's weather).

2. **Exclusion consistency:** Days flagged `forecast_exclude=True` (anomalous partials, opening ramp)
   are removed from both the training history and the test targets for **all four models** — same
   flag, same set, every time.

3. **Small sample:** ~86 operating days (~12 weeks). A regression has very few training samples,
   especially for rainy-day events. Report sample sizes and do not over-claim.

4. **Walk-forward, leakage-free:** every forecast is made using only data strictly before the
   forecast make-date. No hindsight is used.

---

## Output files

- `data/actuals.csv` — daily actuals from BQ (not committed)
- `data/weather_daily.csv` — Open-Meteo observed weather (not committed)
- `data/weather_forecast.csv` — persistence-proxy forecast weather per (make_date, target_date) (not committed)
- `out/report.html` — **self-contained HTML report** with charts and comparison band; open in any browser
- `out/backtest_by_day.csv` — per-(date, horizon, wx_mode, model) rows with forecast/actual/error
- `out/summary_matrix.csv` — aggregated MAPE/MAE/bias by model × horizon and model × DOW
