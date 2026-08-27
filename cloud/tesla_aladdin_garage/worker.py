"""Poll Tesla location; on geofence enter, open the pinned Aladdin door."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from cloud.tesla_aladdin_garage.geofence import Geofence, load_radii, offset_point
from cloud.tesla_aladdin_garage import persist
from cloud.tesla_aladdin_garage.notify import send_garage_email
from skills.aladdin_connect.client import AladdinConnectClient, door_is_open
from skills.tesla_fleet.client import TeslaFleetClient, TeslaFleetError

log = logging.getLogger("tesla_aladdin_garage")


@dataclass
class WorkerConfig:
    vin: str
    home_lat: float
    home_lon: float
    enter_m: float = 500.0
    hysteresis_m: float = 80.0
    cooldown_s: float = 600.0
    poll_s: float = 20.0
    door_serial: str = ""
    door_index: int = 1
    door_name: str = "Big Peach"
    dry_run: bool = True
    telemetry: bool = False
    telemetry_host: str = ""
    telemetry_ca: str = ""
    telemetry_port: int = 443
    location_delta_m: float = 80.0

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        telemetry = os.environ.get("TESLA_TELEMETRY", "0") not in ("0", "", "false", "False")
        poll_default = "0" if telemetry else "20"
        enter_m, hysteresis_m = load_radii()
        return cls(
            vin=os.environ.get("TESLA_VIN", ""),
            home_lat=float(os.environ.get("HOME_LAT", "0") or 0),
            home_lon=float(os.environ.get("HOME_LON", "0") or 0),
            enter_m=enter_m,
            hysteresis_m=hysteresis_m,
            cooldown_s=float(os.environ.get("OPEN_COOLDOWN_S", "600")),
            poll_s=float(os.environ.get("POLL_INTERVAL_S", poll_default)),
            door_serial=os.environ.get("ALADDIN_DEVICE_SERIAL", ""),
            door_index=int(os.environ.get("ALADDIN_DOOR_INDEX", "1")),
            door_name=os.environ.get("ALADDIN_DOOR_NAME", "Big Peach"),
            dry_run=os.environ.get("ALADDIN_DRY_RUN", "1") != "0",
            telemetry=telemetry,
            telemetry_host=os.environ.get("TESLA_TELEMETRY_HOST", "").strip(),
            telemetry_ca=os.environ.get("TESLA_TELEMETRY_CA", "").strip(),
            telemetry_port=int(os.environ.get("TESLA_TELEMETRY_PORT", "443") or 443),
            location_delta_m=float(os.environ.get("LOCATION_MIN_DELTA_M", "80")),
        )

    def apply_overlay(self, overlay: dict) -> None:
        # enter_m / hysteresis_m live only in geofence.json — never overlay.
        if overlay.get("cooldown_s") is not None:
            self.cooldown_s = float(overlay["cooldown_s"])
        if overlay.get("poll_s") is not None:
            self.poll_s = float(overlay["poll_s"])


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
    simulated_enter: bool = False


class GarageWorker:
    def __init__(
        self,
        cfg: WorkerConfig,
        tesla: TeslaFleetClient,
        aladdin: AladdinConnectClient,
        *,
        now: Callable[[], float] = time.time,
        notify: Optional[Callable] = None,
    ):
        self.cfg = cfg
        self.tesla = tesla
        self.aladdin = aladdin
        self._now = now
        self._notify = notify or send_garage_email
        self.geofence = Geofence(cfg.home_lat, cfg.home_lon, cfg.enter_m, cfg.hysteresis_m)
        self.state = WorkerState()
        self._stop = threading.Event()
        persist.clear_geofence_overlay()
        overlay = persist.load_config()
        if overlay:
            self.apply_overlay(overlay)
        if getattr(tesla, "on_billable", None) is None:
            tesla.on_billable = persist.record_billable

    def apply_overlay(self, overlay: dict) -> None:
        self.cfg.apply_overlay(overlay)
        self.geofence.enter_m = self.cfg.enter_m
        self.geofence.hysteresis_m = self.cfg.hysteresis_m
        log.info(
            "tesla-aladdin-garage config enter_m=%s hyst_m=%s cooldown_s=%s",
            self.cfg.enter_m,
            self.cfg.hysteresis_m,
            self.cfg.cooldown_s,
        )

    def _snapshot(self) -> dict:
        return {
            "last_event": self.state.last_event,
            "last_distance_m": self.state.last_distance_m,
            "last_error": self.state.last_error,
            "last_poll_ts": self.state.last_poll_ts,
            "polls": self.state.polls,
            "opens": self.state.opens,
            "needs_reauth": self.state.needs_reauth,
            "enter_m": self.cfg.enter_m,
            "dry_run": self.cfg.dry_run,
            "telemetry": self.cfg.telemetry,
            "poll_s": self.cfg.poll_s,
        }

    def _persist(self) -> None:
        persist.save_state(self._snapshot())

    def stop(self) -> None:
        self._stop.set()

    def observe_fix(
        self,
        lat: float,
        lon: float,
        shift: Optional[str] = None,
        *,
        source: str = "rest",
        count: bool = True,
    ) -> str:
        """Apply one GPS fix to the geofence. Never wakes the car."""
        if count:
            self.state.polls += 1
            self.state.last_poll_ts = self._now()
        self.state.simulated_enter = False
        dist = self.geofence.distance_m(float(lat), float(lon))
        self.state.last_distance_m = dist
        if shift is not None:
            self.state.last_shift = shift
        event = self.geofence.observe(float(lat), float(lon))
        self.state.last_event = event
        log.info(
            "tesla-aladdin-garage poll vin=%s dist_m=%.1f event=%s shift=%s dry_run=%s source=%s",
            self.cfg.vin,
            dist,
            event,
            shift,
            self.cfg.dry_run,
            source,
        )
        if event == "enter":
            result = self._maybe_open()
            self._persist()
            return result
        self._persist()
        return event

    def tick(self) -> str:
        """One REST poll. Never wakes the car."""
        self.state.polls += 1
        self.state.last_poll_ts = self._now()
        self.state.simulated_enter = False
        if self.tesla.needs_user_auth():
            self.state.needs_reauth = True
            self.state.last_error = "missing_refresh_token"
            log.error("tesla-aladdin-garage fail reason=missing_refresh_token action=reauthorize")
            self._persist()
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
        return self.observe_fix(
            float(lat),
            float(lon),
            loc.get("shift_state"),
            source="rest",
            count=False,
        )

    def ensure_telemetry_config(self) -> dict:
        """POST fleet_telemetry_config via Vehicle Command proxy only.

        Cloud Run has no path to GCE localhost:4443; skip with
        telemetry_config_needs_proxy. Signed configure is
        gce_signed_telemetry_config on the telemetry VM.
        """
        host = self.cfg.telemetry_host
        if not host:
            log.info("tesla-aladdin-garage skip reason=telemetry_host_unconfigured")
            return {"ok": False, "reason": "telemetry_host_unconfigured"}
        if self.tesla.needs_user_auth():
            return {"ok": False, "reason": "needs_reauth"}
        proxy = getattr(self.tesla, "command_proxy_url", "") or ""
        if not proxy:
            log.info("tesla-aladdin-garage skip reason=telemetry_config_needs_proxy")
            return {"ok": False, "reason": "telemetry_config_needs_proxy"}
        try:
            resp = self.tesla.put_fleet_telemetry_config(
                vins=[self.cfg.vin],
                hostname=host,
                port=self.cfg.telemetry_port,
                ca=self.cfg.telemetry_ca,
                interval_seconds=15,
                minimum_delta=self.cfg.location_delta_m,
            )
            log.info(
                "tesla-aladdin-garage telemetry_config host=%s port=%s",
                host,
                self.cfg.telemetry_port,
            )
            return {"ok": True, "response": resp}
        except TeslaFleetError as e:
            log.error(
                "tesla-aladdin-garage fail reason=telemetry_config status=%s err=%s",
                e.status,
                e,
            )
            return {"ok": False, "reason": "telemetry_config", "status": e.status, "error": str(e)}

    def current_location(self) -> dict:
        """Live Tesla fix + distance. Does not mutate the geofence."""
        if self.tesla.needs_user_auth():
            self.state.needs_reauth = True
            return {"ok": False, "needs_reauth": True, "error": "missing_refresh_token"}
        try:
            if not self.state.vehicle_id:
                v = self.tesla.find_vehicle(self.cfg.vin)
                self.state.vehicle_id = str(v.get("id") or v.get("vehicle_id") or "")
            loc = self.tesla.vehicle_location(self.state.vehicle_id)
        except TeslaFleetError as e:
            log.error("tesla-aladdin-garage fail reason=location_fetch err=%s", e)
            return {"ok": False, "error": str(e), "status": e.status}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        dist = None
        if lat is not None and lon is not None:
            dist = self.geofence.distance_m(float(lat), float(lon))
            self.state.last_distance_m = dist
        log.info(
            "tesla-aladdin-garage location lat=%s lon=%s dist_m=%s shift=%s",
            lat,
            lon,
            dist,
            loc.get("shift_state"),
        )
        return {
            "ok": True,
            "latitude": lat,
            "longitude": lon,
            "distance_m": dist,
            "shift_state": loc.get("shift_state"),
            "enter_m": self.cfg.enter_m,
            "home_lat": self.cfg.home_lat,
            "home_lon": self.cfg.home_lon,
        }

    def simulate_enter(self) -> str:
        """Force outside → enter → maybe open. Used for live evidence without driving."""
        outside_m = self.cfg.enter_m + self.cfg.hysteresis_m + 50.0
        olat, olon = offset_point(self.cfg.home_lat, self.cfg.home_lon, outside_m, 0.0)
        self.geofence.inside = None
        first = self.geofence.observe(olat, olon)
        log.info(
            "tesla-aladdin-garage simulate phase=outside event=%s dist_m=%.1f",
            first,
            self.geofence.distance_m(olat, olon),
        )
        tesla_m = self.state.last_distance_m
        event = self.geofence.observe(self.cfg.home_lat, self.cfg.home_lon)
        self.state.last_event = event
        # Keep last live Tesla metres for the email (0 would be the fake home pin).
        self.state.last_distance_m = tesla_m if tesla_m is not None else outside_m
        self.state.simulated_enter = True
        log.info(
            "tesla-aladdin-garage simulate event=%s tesla_dist_m=%s approach_m=%.1f",
            event,
            self.state.last_distance_m,
            outside_m,
        )
        if event == "enter":
            result = self._maybe_open()
            self._persist()
            return result
        self._persist()
        return event

    def _emit(self, event: str, door: Optional[dict] = None, detail: str = "") -> None:
        fields = {
            "door": (door or {}).get("name") or self.cfg.door_name,
            "distance_m": self.state.last_distance_m,
            "enter_m": self.cfg.enter_m,
            "simulated": bool(self.state.simulated_enter),
            "door_status": None if door is None else door.get("status"),
            "vin": self.cfg.vin,
            "detail": detail,
        }
        try:
            self._notify(event, fields)
        except Exception as e:
            log.error("tesla-aladdin-garage fail reason=notify err=%s", e)

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
            if door_is_open(door):
                self.state.last_open_ts = now
                log.info(
                    "tesla-aladdin-garage skip reason=already_open door=%s status=%s dist_m=%s enter_m=%s",
                    door.get("name"),
                    door.get("status"),
                    self.state.last_distance_m,
                    self.cfg.enter_m,
                )
                self._emit(
                    "skip_already_open",
                    door,
                    "Door already open (manual / app). No Aladdin command sent. "
                    "Use this to decide whether to change enter_m.",
                )
                return "skip_already_open"
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
            event = "opened_dry_run" if self.cfg.dry_run else "opened"
            self._emit("opened", door, f"dry_run={self.cfg.dry_run} result={result}")
            return event
        except Exception as e:
            self.state.aladdin_ok = False
            self.state.last_error = str(e)
            log.error("tesla-aladdin-garage fail reason=aladdin_open err=%s", e)
            self._emit("open_error", detail=str(e))
            return "open_error"

    def run_forever(self) -> None:
        log.info(
            "tesla-aladdin-garage start vin=%s enter_m=%s hyst_m=%s dry_run=%s telemetry=%s poll_s=%s",
            self.cfg.vin,
            self.cfg.enter_m,
            self.cfg.hysteresis_m,
            self.cfg.dry_run,
            self.cfg.telemetry,
            self.cfg.poll_s,
        )
        if self.cfg.telemetry:
            self.ensure_telemetry_config()
        heartbeat_s = self.cfg.poll_s if self.cfg.poll_s > 0 else 20.0
        while not self._stop.is_set():
            try:
                if self.cfg.poll_s > 0:
                    self.tick()
                else:
                    self.state.last_poll_ts = self._now()
                    log.info(
                        "tesla-aladdin-garage poll vin=%s dist_m=%s event=%s shift=%s dry_run=%s source=heartbeat",
                        self.cfg.vin,
                        self.state.last_distance_m,
                        self.state.last_event,
                        self.state.last_shift,
                        self.cfg.dry_run,
                    )
            except Exception as e:
                self.state.last_error = str(e)
                log.error("tesla-aladdin-garage fail reason=tick_crash err=%s", e)
            self._stop.wait(heartbeat_s)
