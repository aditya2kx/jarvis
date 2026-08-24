"""Tesla Fleet client unit tests — no network."""

import pytest

from skills.tesla_fleet.client import TeslaFleetError, vehicle_data_path


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
