"""Home geofence with enter radius + hysteresis. Pure functions for tests."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


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
    enter_m: float = 400.0
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
