"""Fetch daily historical weather from Open-Meteo for the store's date range.

No API key required. Writes data/weather_daily.csv.
Caches and skips re-fetch if the file already covers the required range.

Store: Palmetto Superfoods, 1900 Aldrich St, Austin, TX 78723 (Mueller).
Coordinates: 30.2978 N, -97.7036 W (verified below).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import urllib.request
import urllib.parse

SPIKE_DIR = os.path.dirname(os.path.abspath(__file__))
ACTUALS_PATH = os.path.join(SPIKE_DIR, "data", "actuals.csv")
OUT_PATH = os.path.join(SPIKE_DIR, "data", "weather_daily.csv")

# Palmetto Superfoods, 1900 Aldrich St, Austin TX 78723 (Mueller district)
STORE_LAT = 30.2978
STORE_LON = -97.7036
STORE_ADDRESS = "1900 Aldrich St, Austin, TX 78723"

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
    "date", "tmax_f", "tmin_f", "tmean_f",
    "precip_in", "rain_in", "snow_in",
    "precip_hours", "wind_max_mph", "weather_code",
]


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _geocode(address: str) -> tuple[float, float]:
    """Try Open-Meteo geocoding. Fall back to hardcoded coords on error."""
    try:
        params = urllib.parse.urlencode({"name": "Palmetto Superfoods Austin", "count": 1, "language": "en", "format": "json"})
        data = _get(f"https://geocoding-api.open-meteo.com/v1/search?{params}")
        results = data.get("results") or []
        if results:
            lat = float(results[0]["latitude"])
            lon = float(results[0]["longitude"])
            print(f"  Geocoded '{address}' → {lat:.4f} N, {lon:.4f} W")
            return lat, lon
    except Exception as e:
        print(f"  Geocoding failed ({e}), using hardcoded coords")
    print(f"  Using hardcoded coords for '{address}': {STORE_LAT} N, {STORE_LON} W")
    return STORE_LAT, STORE_LON


def _read_actuals_date_range() -> tuple[str, str]:
    """Read min/max date from actuals.csv."""
    if not os.path.exists(ACTUALS_PATH):
        raise FileNotFoundError(
            f"actuals.csv not found at {ACTUALS_PATH}. Run pull_actuals.py first."
        )
    with open(ACTUALS_PATH) as f:
        reader = csv.DictReader(f)
        dates = [row["date"] for row in reader if row["date"]]
    if not dates:
        raise RuntimeError("actuals.csv is empty")
    return min(dates), max(dates)


def _cache_covers(start: str, end: str) -> bool:
    """Return True if weather_daily.csv already covers start..end."""
    if not os.path.exists(OUT_PATH):
        return False
    with open(OUT_PATH) as f:
        reader = csv.DictReader(f)
        dates = [row["date"] for row in reader if row.get("date")]
    if not dates:
        return False
    return min(dates) <= start and max(dates) >= end


def fetch_weather(lat: float, lon: float, start: str, end: str) -> list[dict]:
    """Fetch from Open-Meteo archive API."""
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": ",".join(WEATHER_COLS),
        "timezone": "America/Chicago",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "wind_speed_unit": "mph",
    })
    url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
    print(f"  Fetching: {url[:100]}…")
    data = _get(url)
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if not dates:
        raise RuntimeError("No daily data returned from Open-Meteo")

    rows = []
    for i, date in enumerate(dates):
        rows.append({
            "date": date,
            "tmax_f": daily.get("temperature_2m_max", [None])[i],
            "tmin_f": daily.get("temperature_2m_min", [None])[i],
            "tmean_f": daily.get("temperature_2m_mean", [None])[i],
            "precip_in": daily.get("precipitation_sum", [None])[i],
            "rain_in": daily.get("rain_sum", [None])[i],
            "snow_in": daily.get("snowfall_sum", [None])[i],
            "precip_hours": daily.get("precipitation_hours", [None])[i],
            "wind_max_mph": daily.get("wind_speed_10m_max", [None])[i],
            "weather_code": daily.get("weather_code", [None])[i],
        })
    return rows


def main() -> None:
    os.makedirs(os.path.join(SPIKE_DIR, "data"), exist_ok=True)

    start, end = _read_actuals_date_range()
    print(f"Actuals date range: {start} → {end}")

    if _cache_covers(start, end):
        print(f"  Cache hit — weather_daily.csv already covers {start}…{end}. Skipping fetch.")
        return

    lat, lon = _geocode(STORE_ADDRESS)

    rows = fetch_weather(lat, lon, start, end)

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLS)
        writer.writeheader()
        writer.writerows(rows)

    precip_days = sum(1 for r in rows if r.get("precip_in") and float(r["precip_in"]) > 0.1)
    print(f"  Wrote {len(rows)} rows → {OUT_PATH}")
    print(f"  Days with precipitation > 0.1 in: {precip_days}")
    print("Done.")


if __name__ == "__main__":
    main()
