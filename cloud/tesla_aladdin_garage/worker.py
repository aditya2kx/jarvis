"""Poll Tesla location; on geofence enter, open the pinned Aladdin door."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from cloud.tesla_aladdin_garage.geofence import Geofence
from skills.aladdin_connect.client import AladdinConnectClient
from skills.tesla_fleet.client import TeslaFleetClient, TeslaFleetError

log = logging.getLogger("tesla_aladdin_garage")


@dataclass
class WorkerConfig:
    vin: str
    home_lat: float
    home_lon: float
    enter_m: float = 400.0
    hysteresis_m: float = 80.0
    cooldown_s: float = 600.0
    poll_s: float = 20.0
    door_serial: str = ""
    door_index: int = 1
    door_name: str = "Big Peach"
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            vin=os.environ.get("TESLA_VIN", ""),
            home_lat=float(os.environ.get("HOME_LAT", "0") or 0),
            home_lon=float(os.environ.get("HOME_LON", "0") or 0),
            enter_m=float(os.environ.get("GEOFENCE_ENTER_M", "400")),
            hysteresis_m=float(os.environ.get("GEOFENCE_HYSTERESIS_M", "80")),
            cooldown_s=float(os.environ.get("OPEN_COOLDOWN_S", "600")),
            poll_s=float(os.environ.get("POLL_INTERVAL_S", "20")),
            door_serial=os.environ.get("ALADDIN_DEVICE_SERIAL", ""),
            door_index=int(os.environ.get("ALADDIN_DOOR_INDEX", "1")),
            door_name=os.environ.get("ALADDIN_DOOR_NAME", "Big Peach"),
            dry_run=os.environ.get("ALADDIN_DRY_RUN", "1") != "0",
        )


@dataclass
class WorkerState:
    last_event: str = "boot"
    last_distance_m: Optional[float] = None
    last_shift: Optional[str] = None
    last_open_ts: Optional[float] = None
    last_error: str = ""
    last_poll_ts: float = 0.0
    tesla_ok: bool = False
    aladdin_ok: bool = False
    needs_reauth: bool = False
    vehicle_id: Optional[str] = None
    polls: int = 0
    opens: int = 0


class GarageWorker:
    def __init__(
        self,
        cfg: WorkerConfig,
        tesla: TeslaFleetClient,
        aladdin: AladdinConnectClient,
        *,
        now: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.tesla = tesla
        self.aladdin = aladdin
        self._now = now
        self.geofence = Geofence(cfg.home_lat, cfg.home_lon, cfg.enter_m, cfg.hysteresis_m)
        self.state = WorkerState()
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> str:
        """One poll. Never wakes the car."""
        self.state.polls += 1
        self.state.last_poll_ts = self._now()
        if self.tesla.needs_user_auth():
            self.state.needs_reauth = True
            self.state.last_error = "missing_refresh_token"
            log.error("tesla-aladdin-garage fail reason=missing_refresh_token action=reauthorize")
            return "needs_reauth"
        try:
            if not self.state.vehicle_id:
                v = self.tesla.find_vehicle(self.cfg.vin)
                self.state.vehicle_id = str(v.get("id") or v.get("vehicle_id") or "")
            loc = self.tesla.vehicle_location(self.state.vehicle_id)
            self.state.tesla_ok = True
            self.state.needs_reauth = False
        except TeslaFleetError as e:
            self.state.tesla_ok = False
            self.state.last_error = str(e.status or e)
            if e.status in (401, 403):
                self.state.needs_reauth = True
                log.error("tesla-aladdin-garage fail reason=tesla_auth status=%s", e.status)
                return "needs_reauth"
            if e.status in (408, 429):
                log.info("tesla-aladdin-garage skip reason=vehicle_unavailable status=%s", e.status)
                return "skip_asleep"
            log.error("tesla-aladdin-garage fail reason=tesla_poll status=%s err=%s", e.status, e)
            return "error"

        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None:
            log.info("tesla-aladdin-garage skip reason=no_fix vin=%s", self.cfg.vin)
            return "skip_no_fix"
        dist = self.geofence.distance_m(float(lat), float(lon))
        self.state.last_distance_m = dist
        self.state.last_shift = loc.get("shift_state")
        event = self.geofence.observe(float(lat), float(lon))
        self.state.last_event = event
        log.info(
            "tesla-aladdin-garage poll vin=%s dist_m=%.1f event=%s shift=%s dry_run=%s",
            self.cfg.vin,
            dist,
            event,
            loc.get("shift_state"),
            self.cfg.dry_run,
        )
        if event == "enter":
            return self._maybe_open()
        return event

    def _maybe_open(self) -> str:
        now = self._now()
        if self.state.last_open_ts is not None and now - self.state.last_open_ts < self.cfg.cooldown_s:
            log.info("tesla-aladdin-garage skip reason=cooldown vin=%s", self.cfg.vin)
            return "skip_cooldown"
        try:
            door = self.aladdin.resolve_door(
                serial=self.cfg.door_serial,
                name=self.cfg.door_name,
                door_index=self.cfg.door_index,
            )
            self.aladdin.dry_run = self.cfg.dry_run
            result = self.aladdin.open_door(door["device_id"], int(door["door_index"]))
            self.state.aladdin_ok = True
            self.state.last_open_ts = now
            self.state.opens += 1
            log.info(
                "tesla-aladdin-garage open door=%s serial=%s dry_run=%s result=%s",
                door.get("name"),
                door.get("serial"),
                self.cfg.dry_run,
                result,
            )
            return "opened_dry_run" if self.cfg.dry_run else "opened"
        except Exception as e:
            self.state.aladdin_ok = False
            self.state.last_error = str(e)
            log.error("tesla-aladdin-garage fail reason=aladdin_open err=%s", e)
            return "open_error"

    def run_forever(self) -> None:
        log.info(
            "tesla-aladdin-garage start vin=%s enter_m=%s hyst_m=%s dry_run=%s",
            self.cfg.vin,
            self.cfg.enter_m,
            self.cfg.hysteresis_m,
            self.cfg.dry_run,
        )
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self.state.last_error = str(e)
                log.error("tesla-aladdin-garage fail reason=tick_crash err=%s", e)
            self._stop.wait(self.cfg.poll_s)
