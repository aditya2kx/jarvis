"""Home geofence with enter radius + hysteresis. Pure functions for tests."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_RADII_PATH = Path(__file__).with_name("geofence.json")

ENTER_M_BOUNDS = (50.0, 2000.0)
HYSTERESIS_M_BOUNDS = (0.0, 500.0)


def validate_radii(enter_m: float, hysteresis_m: float) -> None:
    """Raise ValueError when a radius would make the fence useless or absurd.

    Below 50 m the car is already in the driveway before the door starts moving;
    above 2000 m it would open from streets away.
    """
    lo, hi = ENTER_M_BOUNDS
    if not lo <= enter_m <= hi:
        raise ValueError(f"enter_m={enter_m} outside {lo}-{hi} m")
    lo, hi = HYSTERESIS_M_BOUNDS
    if not lo <= hysteresis_m <= hi:
        raise ValueError(f"hysteresis_m={hysteresis_m} outside {lo}-{hi} m")


def load_radii(path: Path | None = None) -> tuple[float, float]:
    """Return (enter_m, hysteresis_m) from geofence.json — the bootstrap seed.

    Firestore config wins at runtime; this file is what a fresh instance starts
    from when Firestore holds no radius.
    """
    raw = json.loads((path or _RADII_PATH).read_text())
    enter_m = float(raw["enter_m"])
    hysteresis_m = float(raw["hysteresis_m"])
    validate_radii(enter_m, hysteresis_m)
    return enter_m, hysteresis_m


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class Geofence:
    home_lat: float
    home_lon: float
    enter_m: float = 300.0
    hysteresis_m: float = 80.0
    inside: Optional[bool] = field(default=None)

    @property
    def exit_m(self) -> float:
        return self.enter_m + self.hysteresis_m

    def distance_m(self, lat: float, lon: float) -> float:
        return haversine_m(self.home_lat, self.home_lon, lat, lon)

    def observe(self, lat: float, lon: float) -> str:
        """Return enter | exit | inside | outside. First sample never 'enter'."""
        dist = self.distance_m(lat, lon)
        if self.inside is None:
            self.inside = dist <= self.enter_m
            return "inside" if self.inside else "outside"
        if self.inside:
            if dist > self.exit_m:
                self.inside = False
                return "exit"
            return "inside"
        if dist <= self.enter_m:
            self.inside = True
            return "enter"
        return "outside"


def offset_point(lat: float, lon: float, dist_m: float, bearing_deg: float = 0.0) -> tuple[float, float]:
    """Approximate dest point (metres). 1 deg lat ≈ 111_320 m."""
    bearing = math.radians(bearing_deg)
    dlat = (dist_m * math.cos(bearing)) / 111320.0
    dlon = (dist_m * math.sin(bearing)) / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon
