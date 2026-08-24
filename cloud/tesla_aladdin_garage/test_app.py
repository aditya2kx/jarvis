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


def test_telemetry_official_dispatcher_enter():
    """teslamotors HTTP-dispatcher JSON → outside then enter (no REST poll)."""
    import json
    from pathlib import Path
    from unittest.mock import MagicMock

    from cloud.tesla_aladdin_garage.worker import GarageWorker, WorkerConfig

    testdata = Path(__file__).parent / "testdata"
    outside = json.loads((testdata / "dispatcher_outside.json").read_text())
    enter = json.loads((testdata / "dispatcher_enter.json").read_text())
    tesla = MagicMock()
    tesla.needs_user_auth.return_value = False
    tesla.partner_domain = "yuejj.fleetkey.net"
    aladdin = MagicMock()
    aladdin.resolve_door.return_value = {
        "device_id": "dev",
        "door_index": 1,
        "name": "Big Peach",
        "serial": "F0AD4E3E7403",
        "status": 0,
    }
    aladdin.open_door.return_value = {"ok": True, "dry_run": True}
    w = GarageWorker(
        WorkerConfig(
            vin="7SAYGAEE2TF605512",
            home_lat=29.464083,
            home_lon=-95.517465,
            dry_run=True,
            telemetry=True,
            poll_s=0,
        ),
        tesla,
        aladdin,
    )
    garage_app._worker = w
    c = _client()
    r1 = c.post("/telemetry", headers={"X-Garage-Token": "test-token"}, json=outside)
    assert r1.get_json()["event"] == "outside"
    r2 = c.post("/telemetry", headers={"X-Garage-Token": "test-token"}, json=enter)
    body = r2.get_json()
    assert r2.status_code == 200
    assert body["ok"] is True
    assert body["event"] in ("enter", "opened_dry_run")
    tesla.vehicle_location.assert_not_called()
    aladdin.open_door.assert_called_once()
