"""Map official fleet-telemetry MQTT Location messages to garage POST /telemetry JSON.

MQTT shape (teslamotors/fleet-telemetry datastore/mqtt): topic
`{topic_base}/{vin}/v/Location` and JSON `{"latitude": …, "longitude": …}`.
"""

from __future__ import annotations

import json
from typing import Any, Optional

LOCATION_KEYS = ("Location", "location")


def _loads(raw: Any) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw


def parse_mqtt_topic(topic: str) -> tuple[str, str, str]:
    """Return (vin, kind, field). kind is `v` / `connectivity` / ``."""
    parts = [p for p in str(topic).split("/") if p]
    if len(parts) >= 4 and parts[-2] == "v":
        return parts[-3], "v", parts[-1]
    if len(parts) >= 3 and parts[-1] == "connectivity":
        return parts[-2], "connectivity", "connectivity"
    return "", "", ""


def garage_body_from_mqtt(
    topic: str,
    payload: Any,
    *,
    expected_vin: str = "",
) -> Optional[dict]:
    """Return a POST /telemetry body, or None if this message is not a Location fix."""
    vin, kind, field = parse_mqtt_topic(topic)
    if kind != "v" or field not in LOCATION_KEYS:
        return None
    if expected_vin and vin and vin.strip().upper() != expected_vin.strip().upper():
        return None
    data = _loads(payload)
    if data is None:
        return None
    if isinstance(data, str):
        data = _loads(data)
    loc = data
    if isinstance(data, dict) and "latitude" not in data and "locationValue" in data:
        loc = data.get("locationValue")
    if not isinstance(loc, dict):
        return None
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        return None
    return {
        "vin": vin or expected_vin,
        "data": [
            {
                "key": "Location",
                "value": {"locationValue": {"latitude": lat, "longitude": lon}},
            }
        ],
    }
