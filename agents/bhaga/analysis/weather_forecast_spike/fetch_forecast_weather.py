"""Fetch historical NWP weather forecasts from Open-Meteo forecast archive.

This is DIFFERENT from fetch_weather.py (which fetches observed/reanalysis
weather via archive-api.open-meteo.com).  This script uses the
historical-forecast-api.open-meteo.com endpoint, which returns what the
numerical weather prediction (NWP) model *actually forecast* as of a given
initialization date.

Why this matters for the backtest:
    fetch_weather.py retrieves observed weather → measures the UPPER BOUND of
    weather's value (as if we had perfect forecasts).
    This script retrieves NWP forecast skill → realistic estimate for production,
    where we'd be feeding next-day to 7-day weather forecasts, not actuals.

Usage:
    Run after pull_actuals.py and fetch_weather.py:

        python fetch_forecast_weather.py

    Writes data/weather_forecast.csv with columns:
        make_date, target_date, tmax_f, tmin_f, tmean_f,
        precip_in, rain_in, snow_in, precip_hours, wind_max_mph, weather_code

    One row per (make_date, target_date) pair, covering all make_dates
    needed for the backtest at horizons 1, 3, 7 days.

    Skips fetching if the cache already covers all required pairs.
"""
from __future__ import annotations

import csv
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

SPIKE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTUALS_PATH = os.path.join(SPIKE_DIR, "data", "actuals.csv")
OUT_PATH = os.path.join(SPIKE_DIR, "data", "weather_forecast.csv")

STORE_LAT = 30.2978
STORE_LON = -97.7036

# Horizons for which we want NWP forecast weather.
# 14-day forecasts are unreliable; we deliberately stop at 7.
FORECAST_HORIZONS = [1, 3, 7]
FORECAST_DAYS_PER_CALL = 8  # request 8 days from each init date (covers h=7 + 1 buffer)

# Minimum operating days needed before make_date (must match run_backtest.py).
MIN_WARMUP_DAYS = 28

WEATHER_COLS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "precipitation_hours",
    "wind_speed_10m_max",
    "weather_code",
]

CSV_COLS = [
    "make_date", "target_date",
    "tmax_f", "tmin_f", "tmean_f",
    "precip_in", "rain_in", "snow_in",
    "precip_hours", "wind_max_mph", "weather_code",
]


# ── Helpers ────────────────────────────────────────────────────────────────

def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _load_actuals_operating_days() -> list[str]:
    """Return sorted list of non-excluded operating dates."""
    if not os.path.exists(ACTUALS_PATH):
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
            if fe in ("true", "1", "yes"):
                continue
            rows.append(r["date"])
    return sorted(rows)


def _required_make_dates(operating_days: list[str]) -> set[str]:
    """Compute all make_dates needed for the backtest.

    For each non-excluded operating day D and each horizon H, the make_date
    is D − (H − 1).  We only include make_dates where at least MIN_WARMUP_DAYS
    operating days exist before that make_date.
    """
    needed: set[str] = set()
    for i, target_iso in enumerate(operating_days):
        target = datetime.date.fromisoformat(target_iso)
        for h in FORECAST_HORIZONS:
            make = target - datetime.timedelta(days=h - 1)
            make_iso = make.isoformat()
            # Count operating days strictly before make_date
            prior_count = sum(1 for d in operating_days if d < make_iso)
            if prior_count >= MIN_WARMUP_DAYS:
                needed.add(make_iso)
    return needed


def _load_cache() -> dict[tuple[str, str], bool]:
    """Return set of (make_date, target_date) pairs already in the cache."""
    if not os.path.exists(OUT_PATH):
        return {}
    cached: dict[tuple[str, str], bool] = {}
    with open(OUT_PATH) as f:
        for r in csv.DictReader(f):
            k = (r.get("make_date", ""), r.get("target_date", ""))
            if k[0] and k[1]:
                cached[k] = True
    return cached


def fetch_nwp_forecast(lat: float, lon: float, issue_date: str) -> list[dict]:
    """Fetch the NWP forecast initialized on issue_date.

    Returns a list of daily forecast rows for issue_date through
    issue_date + FORECAST_DAYS_PER_CALL − 1.

    The historical-forecast-api endpoint interprets start_date/end_date as
    the forecast issue (initialization) date range.  Each day in that range
    contributes one set of `forecast_days` rows to the response, but in
    practice Open-Meteo returns the forecast as a simple time series from
    the first requested initialization through the last forecast offset.

    For our use we call one initialization date at a time and read back the
    time series for the following FORECAST_DAYS_PER_CALL days.
    """
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": issue_date,
        "end_date": issue_date,
        "forecast_days": FORECAST_DAYS_PER_CALL,
        "daily": ",".join(WEATHER_COLS),
        "timezone": "America/Chicago",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "wind_speed_unit": "mph",
    })
    url = (
        f"https://historical-forecast-api.open-meteo.com/v1/forecast?{params}"
    )
    data = _get(url)
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    rows = []
    for i, date in enumerate(dates):
        rows.append(
            {
                "make_date": issue_date,
                "target_date": date,
                "tmax_f": daily.get("temperature_2m_max", [None])[i],
                "tmin_f": daily.get("temperature_2m_min", [None])[i],
                "tmean_f": daily.get("temperature_2m_mean", [None])[i],
                "precip_in": daily.get("precipitation_sum", [None])[i],
                "rain_in": daily.get("rain_sum", [None])[i],
                "snow_in": daily.get("snowfall_sum", [None])[i],
                "precip_hours": daily.get("precipitation_hours", [None])[i],
                "wind_max_mph": daily.get("wind_speed_10m_max", [None])[i],
                "weather_code": daily.get("weather_code", [None])[i],
            }
        )
    return rows


def main() -> None:
    print("Computing required make_dates for backtest…")
    ops = _load_actuals_operating_days()
    if not ops:
        print("ERROR: no operating days found in actuals.csv", file=sys.stderr)
        sys.exit(1)

    needed = _required_make_dates(ops)
    print(f"  {len(needed)} unique make_dates needed (horizons {FORECAST_HORIZONS})")

    cache = _load_cache()
    # We need each make_date to appear in the cache for at least one pair;
    # simplify to re-fetch if any make_date is entirely missing.
    cached_make_dates = set(k[0] for k in cache)
    missing = sorted(needed - cached_make_dates)

    if not missing:
        print(
            f"  Cache hit — all {len(needed)} make_dates already in {OUT_PATH}. Skipping."
        )
        return

    print(
        f"  {len(cached_make_dates)} cached, {len(missing)} to fetch"
        f" ({missing[0]} … {missing[-1]})"
    )

    os.makedirs(os.path.join(SPIKE_DIR, "data"), exist_ok=True)

    # Append mode: write header only when creating the file.
    write_header = not os.path.exists(OUT_PATH) or os.path.getsize(OUT_PATH) == 0
    new_rows: list[dict] = []
    errors: list[str] = []

    for i, make_date in enumerate(missing, 1):
        try:
            rows = fetch_nwp_forecast(STORE_LAT, STORE_LON, make_date)
            new_rows.extend(rows)
            if i % 10 == 0 or i == len(missing):
                print(f"  [{i}/{len(missing)}] {make_date} → {len(rows)} forecast days")
        except Exception as e:
            errors.append(make_date)
            print(f"  Warning: failed for {make_date}: {e}", file=sys.stderr)
        # Polite rate-limiting: Open-Meteo free tier has no hard rate limit
        # but we add a small pause to avoid hammering the server.
        if i < len(missing):
            time.sleep(0.15)

    if new_rows:
        with open(OUT_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
        print(f"  Appended {len(new_rows)} rows → {OUT_PATH}")

    if errors:
        print(
            f"\nWarning: {len(errors)} make_date(s) failed; backtest will fall back to "
            f"observed weather for those dates:\n  " + "\n  ".join(errors),
            file=sys.stderr,
        )
    else:
        print(f"Done. ({len(new_rows)} rows, 0 errors)")


if __name__ == "__main__":
    main()
