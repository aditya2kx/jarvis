"""Admin auth + simulate/location routes."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ["GARAGE_WORKER"] = "0"
os.environ["GARAGE_ADMIN_TOKEN"] = "test-token"

from cloud.tesla_aladdin_garage import app as garage_app


def _client():
    garage_app.app.config["TESTING"] = True
    return garage_app.app.test_client()


def test_tick_unauthorized_without_token():
    garage_app._worker = MagicMock()
    r = _client().post("/tick")
    assert r.status_code == 401


def test_tick_unauthorized_wrong_token():
    garage_app._worker = MagicMock()
    r = _client().post("/tick", headers={"X-Garage-Token": "nope"})
    assert r.status_code == 401


def test_simulate_enter_ok():
    w = MagicMock()
    w.simulate_enter.return_value = "opened"
    garage_app._worker = w
    r = _client().post("/simulate/enter", headers={"X-Garage-Token": "test-token"})
    assert r.status_code == 200
    assert r.get_json()["event"] == "opened"


def test_location_ok():
    w = MagicMock()
    w.current_location.return_value = {"ok": True, "distance_m": 12.5}
    garage_app._worker = w
    r = _client().get("/location", headers={"X-Garage-Token": "test-token"})
    assert r.status_code == 200
    assert r.get_json()["distance_m"] == 12.5


def test_telemetry_unauthorized():
    garage_app._worker = MagicMock()
    r = _client().post("/telemetry", json={"latitude": 1, "longitude": 2})
    assert r.status_code == 401


def test_telemetry_observe_fix():
    w = MagicMock()
    w.cfg.vin = "7SAYGAEE2TF605512"
    w.observe_fix.return_value = "inside"
    garage_app._worker = w
    r = _client().post(
        "/telemetry",
        headers={"X-Garage-Token": "test-token"},
        json={"vin": "7SAYGAEE2TF605512", "latitude": 29.46, "longitude": -95.51},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["event"] == "inside"
    w.observe_fix.assert_called_once()
