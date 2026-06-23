"""Fetch daily weather from Open-Meteo (no API key required).

Provides two entry points:
    fetch_actuals(lat, lon, start, end) -> list[dict]
        ERA5-based historical analysis for past dates (archive endpoint).
        Suitable for backfilling weather_daily actuals.

    fetch_forecast(lat, lon, days) -> list[dict]
        NWP-blended 7–10 day forward forecast starting from today (forecast
        endpoint).  Covers the near-horizon window where weather signal is real.

Both return rows keyed by ISO date:
    {date, tmean_c, tmax_c, tmin_c, precip_mm, is_rainy, kind, source}

kind is 'actual' for archive rows and 'forecast' for forward rows.
source is always 'open_meteo'.

Failure contract:
    Both functions raise WeatherFetchError on network / parse failure.
    Callers in the nightly pipeline MUST wrap in try/except and degrade
    gracefully — an Open-Meteo outage must never fail the prod nightly run.

Conversion notes:
    BQ stores metric (°C, mm).  The ramp forecast module converts to °F/inch
    internally for the weather-feature thresholds derived from the spike
    (heat_flag > 90°F, rainy_flag > 0.25 inch).
"""
from __future__ import annotations

import datetime
import json
import urllib.parse
import urllib.request
from typing import Any

__all__ = [
    "WeatherFetchError",
    "fetch_actuals",
    "fetch_forecast",
    "map_weather_row",
]

# Open-Meteo variables shared by both endpoints.
_DAILY_VARS = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
]
_TIMEOUT = 30  # seconds per HTTP request


class WeatherFetchError(RuntimeError):
    """Raised when weather data cannot be fetched or parsed."""


def _http_get(url: str) -> dict[str, Any]:
    """Fetch url and parse JSON. Raises WeatherFetchError on any failure."""
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        raise WeatherFetchError(f"HTTP GET failed: {url!r}: {exc}") from exc


def _parse_daily(data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Parse Open-Meteo daily response into weather rows."""
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if not dates:
        raise WeatherFetchError("Open-Meteo returned no daily rows")

    tmean = daily.get("temperature_2m_mean") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_sum") or []

    rows = []
    for i, date in enumerate(dates):
        tm = tmean[i] if i < len(tmean) else None
        tx = tmax[i] if i < len(tmax) else None
        tn = tmin[i] if i < len(tmin) else None
        pr = precip[i] if i < len(precip) else 0.0
        # Fallback defaults: Austin TX seasonal average (Celsius)
        if tm is None:
            tm = 20.0
        if tx is None:
            tx = tm + 5.0
        if tn is None:
            tn = tm - 5.0
        if pr is None:
            pr = 0.0
        rows.append({
            "date": date,
            "tmean_c": float(tm),
            "tmax_c": float(tx),
            "tmin_c": float(tn),
            "precip_mm": float(pr),
            "is_rainy": pr > 6.35,  # > 0.25 inch in mm
            "kind": kind,
            "source": "open_meteo",
        })
    return rows


def fetch_actuals(
    lat: float,
    lon: float,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """ERA5 reanalysis weather for date range [start, end] (ISO strings).

    Returns rows with kind='actual'.  May raise WeatherFetchError.
    """
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": ",".join(_DAILY_VARS),
        "timezone": "America/Chicago",
    })
    url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
    data = _http_get(url)
    return _parse_daily(data, kind="actual")


def fetch_forecast(
    lat: float,
    lon: float,
    days: int = 10,
) -> list[dict[str, Any]]:
    """NWP-blended forward forecast for the next ``days`` days.

    Returns rows with kind='forecast'.  May raise WeatherFetchError.
    ``days`` is capped at 16 (Open-Meteo free-tier limit).
    """
    days = min(days, 16)
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "forecast_days": days,
        "daily": ",".join(_DAILY_VARS),
        "timezone": "America/Chicago",
    })
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    data = _http_get(url)
    return _parse_daily(data, kind="forecast")


def map_weather_row(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a weather row to the BQ weather_daily schema.

    Accepts a row as returned by fetch_actuals / fetch_forecast.
    """
    import datetime as _dt

    raw_date = rec.get("date")
    if isinstance(raw_date, str):
        bq_date = _dt.date.fromisoformat(raw_date)
    elif isinstance(raw_date, _dt.date):
        bq_date = raw_date
    else:
        bq_date = None

    return {
        "date": bq_date,
        "tmean_c": float(rec.get("tmean_c") or 20.0),
        "tmax_c": float(rec.get("tmax_c") or 25.0),
        "tmin_c": float(rec.get("tmin_c") or 15.0),
        "precip_mm": float(rec.get("precip_mm") or 0.0),
        "is_rainy": bool(rec.get("is_rainy", False)),
        "kind": str(rec.get("kind", "actual")),
        "source": str(rec.get("source", "open_meteo")),
        "fetched_at": _dt.datetime.now(_dt.timezone.utc),
    }
