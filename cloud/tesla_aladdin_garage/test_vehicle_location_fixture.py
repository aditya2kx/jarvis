"""Fixture parse of captured Tesla vehicle_data."""

import json
from pathlib import Path

from skills.tesla_fleet.client import parse_vehicle_location

FIX = Path(__file__).parent / "testdata" / "vehicle_data.json"


def test_parse_prefers_drive_state():
    raw = json.loads(FIX.read_text())
    loc = parse_vehicle_location(raw)
    assert loc["latitude"] == 29.464083
    assert loc["longitude"] == -95.517465
    assert loc["shift_state"] == "P"


def test_parse_falls_back_to_location_data():
    loc = parse_vehicle_location(
        {"location_data": {"latitude": 1.0, "longitude": 2.0}, "state": "online"}
    )
    assert loc["latitude"] == 1.0
    assert loc["longitude"] == 2.0
