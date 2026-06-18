"""Tests for skills/weather/open_meteo.py."""
from __future__ import annotations

import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

from skills.weather.open_meteo import (
    WeatherFetchError,
    fetch_actuals,
    fetch_forecast,
    map_weather_row,
)


def _make_response(dates: list[str], tmean=None, tmax=None, tmin=None, precip=None):
    """Build a mock Open-Meteo daily response payload."""
    n = len(dates)
    return {
        "daily": {
            "time": dates,
            "temperature_2m_mean": tmean or [20.0] * n,
            "temperature_2m_max": tmax or [25.0] * n,
            "temperature_2m_min": tmin or [15.0] * n,
            "precipitation_sum": precip or [0.0] * n,
        }
    }


class FetchActualsTests(unittest.TestCase):
    """Tests for fetch_actuals()."""

    @patch("skills.weather.open_meteo._http_get")
    def test_returns_correct_row_count(self, mock_get):
        dates = ["2026-06-01", "2026-06-02", "2026-06-03"]
        mock_get.return_value = _make_response(dates)
        rows = fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-03")
        self.assertEqual(len(rows), 3)

    @patch("skills.weather.open_meteo._http_get")
    def test_row_schema(self, mock_get):
        dates = ["2026-06-01"]
        mock_get.return_value = _make_response(dates, tmean=[22.5], tmax=[28.0], tmin=[17.0], precip=[1.5])
        rows = fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        r = rows[0]
        self.assertEqual(r["date"], "2026-06-01")
        self.assertAlmostEqual(r["tmean_c"], 22.5)
        self.assertAlmostEqual(r["tmax_c"], 28.0)
        self.assertAlmostEqual(r["tmin_c"], 17.0)
        self.assertAlmostEqual(r["precip_mm"], 1.5)
        self.assertFalse(r["is_rainy"])
        self.assertEqual(r["kind"], "actual")
        self.assertEqual(r["source"], "open_meteo")

    @patch("skills.weather.open_meteo._http_get")
    def test_kind_is_actual(self, mock_get):
        mock_get.return_value = _make_response(["2026-06-01"])
        rows = fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        self.assertEqual(rows[0]["kind"], "actual")

    @patch("skills.weather.open_meteo._http_get")
    def test_rainy_flag_true_above_threshold(self, mock_get):
        # > 6.35 mm is rainy (= > 0.25 inch)
        mock_get.return_value = _make_response(["2026-06-01"], precip=[10.0])
        rows = fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        self.assertTrue(rows[0]["is_rainy"])

    @patch("skills.weather.open_meteo._http_get")
    def test_rainy_flag_false_below_threshold(self, mock_get):
        mock_get.return_value = _make_response(["2026-06-01"], precip=[2.0])
        rows = fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        self.assertFalse(rows[0]["is_rainy"])

    @patch("skills.weather.open_meteo._http_get")
    def test_none_precip_defaults_to_zero(self, mock_get):
        resp = _make_response(["2026-06-01"])
        resp["daily"]["precipitation_sum"] = [None]
        mock_get.return_value = resp
        rows = fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        self.assertEqual(rows[0]["precip_mm"], 0.0)

    @patch("skills.weather.open_meteo._http_get")
    def test_none_temps_get_defaults(self, mock_get):
        resp = _make_response(["2026-06-01"])
        resp["daily"]["temperature_2m_mean"] = [None]
        resp["daily"]["temperature_2m_max"] = [None]
        resp["daily"]["temperature_2m_min"] = [None]
        mock_get.return_value = resp
        rows = fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        r = rows[0]
        self.assertIsNotNone(r["tmean_c"])
        self.assertIsNotNone(r["tmax_c"])
        self.assertIsNotNone(r["tmin_c"])

    @patch("skills.weather.open_meteo._http_get")
    def test_empty_response_raises(self, mock_get):
        mock_get.return_value = {"daily": {"time": []}}
        with self.assertRaises(WeatherFetchError):
            fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-03")

    @patch("skills.weather.open_meteo._http_get")
    def test_network_error_raises_weather_fetch_error(self, mock_get):
        mock_get.side_effect = WeatherFetchError("timeout")
        with self.assertRaises(WeatherFetchError):
            fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")

    @patch("skills.weather.open_meteo._http_get")
    def test_url_contains_archive_endpoint(self, mock_get):
        mock_get.return_value = _make_response(["2026-06-01"])
        fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        url = mock_get.call_args[0][0]
        self.assertIn("archive-api.open-meteo.com", url)

    @patch("skills.weather.open_meteo._http_get")
    def test_url_contains_lat_lon(self, mock_get):
        mock_get.return_value = _make_response(["2026-06-01"])
        fetch_actuals(30.2978, -97.7036, "2026-06-01", "2026-06-01")
        url = mock_get.call_args[0][0]
        self.assertIn("30.2978", url)
        self.assertIn("-97.7036", url)


class FetchForecastTests(unittest.TestCase):
    """Tests for fetch_forecast()."""

    @patch("skills.weather.open_meteo._http_get")
    def test_kind_is_forecast(self, mock_get):
        mock_get.return_value = _make_response(["2026-06-18"])
        rows = fetch_forecast(30.2978, -97.7036, days=1)
        self.assertEqual(rows[0]["kind"], "forecast")

    @patch("skills.weather.open_meteo._http_get")
    def test_url_contains_forecast_endpoint(self, mock_get):
        mock_get.return_value = _make_response(["2026-06-18"])
        fetch_forecast(30.2978, -97.7036, days=1)
        url = mock_get.call_args[0][0]
        self.assertIn("api.open-meteo.com/v1/forecast", url)

    @patch("skills.weather.open_meteo._http_get")
    def test_days_capped_at_16(self, mock_get):
        mock_get.return_value = _make_response(["2026-06-18"])
        fetch_forecast(30.2978, -97.7036, days=99)
        url = mock_get.call_args[0][0]
        self.assertIn("forecast_days=16", url)

    @patch("skills.weather.open_meteo._http_get")
    def test_returns_rows(self, mock_get):
        dates = [f"2026-06-{18 + i:02d}" for i in range(7)]
        mock_get.return_value = _make_response(dates)
        rows = fetch_forecast(30.2978, -97.7036, days=7)
        self.assertEqual(len(rows), 7)


class MapWeatherRowTests(unittest.TestCase):
    """Tests for map_weather_row()."""

    def test_maps_all_bq_fields(self):
        row = {
            "date": "2026-06-01",
            "tmean_c": 22.0,
            "tmax_c": 28.0,
            "tmin_c": 17.0,
            "precip_mm": 3.0,
            "is_rainy": False,
            "kind": "actual",
            "source": "open_meteo",
        }
        mapped = map_weather_row(row)
        self.assertIsInstance(mapped["date"], datetime.date)
        self.assertEqual(mapped["date"], datetime.date(2026, 6, 1))
        self.assertAlmostEqual(mapped["tmean_c"], 22.0)
        self.assertFalse(mapped["is_rainy"])
        self.assertEqual(mapped["kind"], "actual")
        self.assertIsNotNone(mapped["fetched_at"])

    def test_accepts_date_object(self):
        row = {
            "date": datetime.date(2026, 6, 1),
            "tmean_c": 20.0, "tmax_c": 25.0, "tmin_c": 15.0,
            "precip_mm": 0.0, "is_rainy": False,
            "kind": "forecast", "source": "open_meteo",
        }
        mapped = map_weather_row(row)
        self.assertEqual(mapped["date"], datetime.date(2026, 6, 1))

    def test_defaults_on_missing_values(self):
        mapped = map_weather_row({"date": "2026-06-01", "kind": "actual", "source": "open_meteo"})
        self.assertAlmostEqual(mapped["tmean_c"], 20.0)
        self.assertAlmostEqual(mapped["precip_mm"], 0.0)
        self.assertFalse(mapped["is_rainy"])


if __name__ == "__main__":
    unittest.main()
