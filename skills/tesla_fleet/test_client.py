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


def test_billable_category():
    from skills.tesla_fleet.client import billable_category

    assert billable_category("/api/1/vehicles/1/vehicle_data") == "data"
    assert billable_category("/api/1/vehicles/1/wake_up") == "wakes"
    assert billable_category("/api/1/vehicles/fleet_telemetry_config") == "commands"


def test_from_env_command_proxy_url(monkeypatch):
    from skills.tesla_fleet.client import TeslaFleetClient

    monkeypatch.setenv("TESLA_CLIENT_ID", "cid")
    monkeypatch.setenv("TESLA_CLIENT_SECRET", "sec")
    monkeypatch.setenv("TESLA_REFRESH_TOKEN", "rt")
    monkeypatch.setenv("TESLA_COMMAND_PROXY_URL", "https://127.0.0.1:4443")
    c = TeslaFleetClient.from_env()
    assert c.command_proxy_url == "https://127.0.0.1:4443"


def test_put_fleet_telemetry_config_uses_proxy_base(monkeypatch):
    from skills.tesla_fleet.client import TeslaFleetClient

    seen = {}

    def fake_http(method, url, headers=None, payload=None, expect_json=True, ssl_context=None):
        seen["method"] = method
        seen["url"] = url
        seen["insecure"] = ssl_context is not None
        return '{"response":{"updated_vehicles":1}}'

    monkeypatch.setattr("skills.tesla_fleet.client._http_json", fake_http)
    c = TeslaFleetClient(
        client_id="cid",
        client_secret="sec",
        refresh_token="rt",
        command_proxy_url="https://127.0.0.1:4443",
    )
    c._access_token = "tok"
    c._access_exp = 9e12
    out = c.put_fleet_telemetry_config(vins=["7SAYGAEE2TF605512"], hostname="host.example", port=8443)
    assert seen["url"] == "https://127.0.0.1:4443/api/1/vehicles/fleet_telemetry_config"
    assert seen["insecure"] is True
    assert out["response"]["updated_vehicles"] == 1


def test_put_fleet_telemetry_config_unsigned_uses_audience(monkeypatch):
    from skills.tesla_fleet.client import TeslaFleetClient, DEFAULT_AUDIENCE

    seen = {}

    def fake_http(method, url, headers=None, payload=None, expect_json=True, ssl_context=None):
        seen["url"] = url
        seen["insecure"] = ssl_context is not None
        return '{"response":{}}'

    monkeypatch.setattr("skills.tesla_fleet.client._http_json", fake_http)
    c = TeslaFleetClient(client_id="cid", client_secret="sec", refresh_token="rt")
    c._access_token = "tok"
    c._access_exp = 9e12
    c.put_fleet_telemetry_config(vins=["7SAYGAEE2TF605512"], hostname="host.example")
    assert seen["url"].startswith(DEFAULT_AUDIENCE.rstrip("/") + "/api/1/vehicles/fleet_telemetry_config")
    assert seen["insecure"] is False


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
