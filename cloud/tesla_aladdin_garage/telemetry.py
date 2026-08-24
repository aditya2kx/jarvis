"""Parse Tesla fleet-telemetry HTTP-dispatcher JSON into lat/lon fixes.

Vehicles speak mTLS WebSocket protobuf to a self-hosted fleet-telemetry server;
that server POSTs JSON here. Cloud Run is the geofence consumer, not the mTLS
terminator.
"""

from __future__ import annotations

from typing import Any, Optional


def _f(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _pair(obj: Any) -> Optional[tuple[float, float]]:
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        lat, lon = _f(obj[0]), _f(obj[1])
        if lat is not None and lon is not None:
            return lat, lon
    if not isinstance(obj, dict):
        return None
    loc = obj.get("locationValue") or obj.get("location") or obj
    if isinstance(loc, (list, tuple)):
        return _pair(loc)
    if isinstance(loc, dict):
        lat = _f(loc.get("latitude") if loc.get("latitude") is not None else loc.get("lat"))
        lon = _f(
            loc.get("longitude")
            if loc.get("longitude") is not None
            else loc.get("lng") if loc.get("lng") is not None else loc.get("lon")
        )
        if lat is not None and lon is not None:
            return lat, lon
    return None


def _from_data_list(items: list, vin: str) -> list[dict]:
    out: list[dict] = []
    shift = None
    pending: list[tuple[float, float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("Key") or "")
        val = item.get("value") if "value" in item else item.get("Value")
        if key in ("ShiftState", "shift_state") and isinstance(val, dict):
            shift = val.get("stringValue") or val.get("string_value") or shift
        elif key in ("ShiftState", "shift_state") and isinstance(val, str):
            shift = val
        pair = _pair(val) if key in ("Location", "location", "") else None
        if pair is None and key in ("Location", "location"):
            pair = _pair(item)
        if pair is not None:
            pending.append(pair)
        elif key in ("Location", "location"):
            nested = _pair(item.get("locationValue") or {})
            if nested:
                pending.append(nested)
    for lat, lon in pending:
        out.append({"vin": vin, "latitude": lat, "longitude": lon, "shift_state": shift})
    return out


def _from_data_map(data: dict, vin: str) -> list[dict]:
    loc = data.get("Location") or data.get("location")
    pair = _pair(loc)
    if pair is None:
        return []
    shift_raw = data.get("ShiftState") or data.get("shift_state")
    shift = None
    if isinstance(shift_raw, dict):
        shift = shift_raw.get("stringValue") or shift_raw.get("string_value")
    elif isinstance(shift_raw, str):
        shift = shift_raw
    return [{"vin": vin, "latitude": pair[0], "longitude": pair[1], "shift_state": shift}]


def _one_payload(payload: dict, expected_vin: str) -> list[dict]:
    vin = str(payload.get("vin") or payload.get("VIN") or "")
    if expected_vin and vin and vin.strip().upper() != expected_vin.strip().upper():
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return _from_data_list(data, vin or expected_vin)
    if isinstance(data, dict):
        found = _from_data_map(data, vin or expected_vin)
        if found:
            return found
    pair = _pair(payload)
    if pair is None:
        loc = payload.get("Location") or payload.get("location") or payload.get("drive_state")
        pair = _pair(loc)
    if pair is None:
        return []
    return [
        {
            "vin": vin or expected_vin,
            "latitude": pair[0],
            "longitude": pair[1],
            "shift_state": payload.get("shift_state"),
        }
    ]


def extract_location_fixes(payload: Any, expected_vin: str = "") -> list[dict]:
    """Return `{vin, latitude, longitude, shift_state}` dicts from dispatcher JSON."""
    if payload is None:
        return []
    if isinstance(payload, list):
        out: list[dict] = []
        for item in payload:
            out.extend(extract_location_fixes(item, expected_vin=expected_vin))
        return out
    if not isinstance(payload, dict):
        return []
    nested = payload.get("payload") or payload.get("record")
    if isinstance(nested, (dict, list)) and "latitude" not in payload and "data" not in payload:
        return extract_location_fixes(nested, expected_vin=expected_vin)
    return _one_payload(payload, expected_vin)
