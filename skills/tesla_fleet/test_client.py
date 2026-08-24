"""Tesla Fleet client unit tests — no network."""

import pytest

from skills.tesla_fleet.client import TeslaFleetError, fleet_telemetry_config_body, vehicle_data_path


def test_vehicle_data_path_uses_semicolon():
    path = vehicle_data_path("123")
    assert "vehicle_data" in path
    query = path.split("endpoints=", 1)[1]
    assert "," not in query
    assert "location_data" in query
    assert "drive_state" in query
    assert ";" in query or "%3B" in query


def test_vehicle_data_path_rejects_comma():
    with pytest.raises(TeslaFleetError, match="semicolon"):
        vehicle_data_path("1", endpoints="location_data,drive_state")


def test_parse_vehicle_location_drive_state():
    from skills.tesla_fleet.client import parse_vehicle_location

    loc = parse_vehicle_location(
        {"drive_state": {"latitude": 1.5, "longitude": 2.5, "shift_state": "D"}}
    )
    assert loc["latitude"] == 1.5
    assert loc["longitude"] == 2.5


def test_fleet_telemetry_config_body_location_delta():
    body = fleet_telemetry_config_body(
        vins=[" 7saygaee2tf605512 "],
        hostname="https://telemetry.example.com/unused",
        minimum_delta=80,
    )
    assert body["vins"] == ["7SAYGAEE2TF605512"]
    assert body["config"]["hostname"] == "telemetry.example.com"
    assert body["config"]["port"] == 443
    loc = body["config"]["fields"]["Location"]
    assert loc["interval_seconds"] == 15
    assert loc["minimum_delta"] == 80.0
    assert "ca" not in body["config"]
