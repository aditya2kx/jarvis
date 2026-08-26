"""Worker: skip already-home, cooldown, dry-run open."""

from __future__ import annotations

from unittest.mock import MagicMock

from cloud.tesla_aladdin_garage.worker import GarageWorker, WorkerConfig

VIN = "7SAYGAEE2TF605512"
HOME = (29.464083, -95.517465)


def _cfg(**kw) -> WorkerConfig:
    base = dict(
        vin=VIN,
        home_lat=HOME[0],
        home_lon=HOME[1],
        enter_m=400,
        hysteresis_m=80,
        cooldown_s=600,
        poll_s=1,
        door_serial="F0AD4E3E7403",
        door_index=1,
        door_name="Big Peach",
        dry_run=True,
    )
    base.update(kw)
    return WorkerConfig(**base)


def _tesla(lat, lon, shift="P"):
    t = MagicMock()
    t.needs_user_auth.return_value = False
    t.find_vehicle.return_value = {"id": "99", "vin": VIN}
    t.vehicle_location.return_value = {
        "latitude": lat,
        "longitude": lon,
        "shift_state": shift,
    }
    return t


def _aladdin():
    a = MagicMock()
    a.resolve_door.return_value = {
        "device_id": "dev",
        "door_index": 1,
        "name": "Big Peach",
        "serial": "F0AD4E3E7403",
    }
    a.open_door.return_value = {"ok": True, "dry_run": True}
    return a


def test_already_open_skips_command_and_notifies():
    tesla, aladdin = _tesla(29.472, HOME[1]), _aladdin()
    aladdin.resolve_door.return_value["status"] = 1
    notes = []
    w = GarageWorker(_cfg(), tesla, aladdin, notify=lambda ev, fields: notes.append((ev, fields)))
    w.tick()
    tesla.vehicle_location.return_value = {
        "latitude": HOME[0],
        "longitude": HOME[1],
        "shift_state": "D",
    }
    assert w.tick() == "skip_already_open"
    aladdin.open_door.assert_not_called()
    assert notes[-1][0] == "skip_already_open"
    assert notes[-1][1]["enter_m"] == 400
    assert notes[-1][1]["distance_m"] is not None
    assert notes[-1][1]["distance_m"] < 400


def test_open_notifies():
    tesla, aladdin = _tesla(29.472, HOME[1]), _aladdin()
    notes = []
    w = GarageWorker(_cfg(), tesla, aladdin, notify=lambda ev, fields: notes.append(ev))
    w.tick()
    tesla.vehicle_location.return_value = {
        "latitude": HOME[0],
        "longitude": HOME[1],
        "shift_state": "D",
    }
    assert w.tick() == "opened_dry_run"
    assert "opened" in notes



def test_already_home_does_not_open():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    w = GarageWorker(_cfg(), tesla, aladdin)
    assert w.tick() == "inside"
    aladdin.open_door.assert_not_called()


def test_enter_opens_dry_run():
    tesla, aladdin = _tesla(29.472, HOME[1]), _aladdin()
    w = GarageWorker(_cfg(), tesla, aladdin)
    assert w.tick() == "outside"
    tesla.vehicle_location.return_value = {
        "latitude": HOME[0],
        "longitude": HOME[1],
        "shift_state": "D",
    }
    assert w.tick() == "opened_dry_run"
    aladdin.open_door.assert_called_once()


def test_cooldown_skips_second_open():
    tesla, aladdin = _tesla(29.472, HOME[1]), _aladdin()
    now = [0.0]
    w = GarageWorker(_cfg(), tesla, aladdin, now=lambda: now[0])
    w.tick()
    tesla.vehicle_location.return_value = {
        "latitude": HOME[0],
        "longitude": HOME[1],
        "shift_state": "D",
    }
    assert w.tick() == "opened_dry_run"
    tesla.vehicle_location.return_value = {
        "latitude": 29.472,
        "longitude": HOME[1],
        "shift_state": "D",
    }
    now[0] = 10
    w.tick()
    tesla.vehicle_location.return_value = {
        "latitude": HOME[0],
        "longitude": HOME[1],
        "shift_state": "D",
    }
    now[0] = 20
    assert w.tick() == "skip_cooldown"
    assert aladdin.open_door.call_count == 1


def test_missing_token_asks_reauth():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    tesla.needs_user_auth.return_value = True
    w = GarageWorker(_cfg(), tesla, aladdin)
    assert w.tick() == "needs_reauth"


def test_simulate_enter_opens_dry_run():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    w = GarageWorker(_cfg(dry_run=True), tesla, aladdin)
    assert w.simulate_enter() == "opened_dry_run"
    aladdin.open_door.assert_called_once()


def test_simulate_enter_cooldown():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    now = [0.0]
    w = GarageWorker(_cfg(), tesla, aladdin, now=lambda: now[0])
    assert w.simulate_enter() == "opened_dry_run"
    now[0] = 10
    assert w.simulate_enter() == "skip_cooldown"
    assert aladdin.open_door.call_count == 1


def test_current_location_does_not_open():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    w = GarageWorker(_cfg(), tesla, aladdin)
    loc = w.current_location()
    assert loc["ok"] is True
    assert loc["distance_m"] < 1
    aladdin.open_door.assert_not_called()


def test_overlay_changes_enter_m():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    w = GarageWorker(_cfg(), tesla, aladdin)
    w.apply_overlay({"enter_m": 350})
    assert w.cfg.enter_m == 350
    assert w.geofence.enter_m == 350


def test_observe_fix_from_telemetry_opens_without_rest_poll():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    w = GarageWorker(_cfg(), tesla, aladdin)
    outside = HOME[0] + 0.01
    assert w.observe_fix(outside, HOME[1], source="telemetry") == "outside"
    tesla.vehicle_location.assert_not_called()
    assert w.observe_fix(HOME[0], HOME[1], source="telemetry") == "opened_dry_run"
    aladdin.open_door.assert_called_once()
    tesla.vehicle_location.assert_not_called()


def test_ensure_telemetry_config_skips_without_host():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    w = GarageWorker(_cfg(), tesla, aladdin)
    assert w.ensure_telemetry_config()["reason"] == "telemetry_host_unconfigured"
    tesla.put_fleet_telemetry_config.assert_not_called()


def test_ensure_telemetry_config_posts_when_host_set():
    tesla, aladdin = _tesla(*HOME), _aladdin()
    tesla.put_fleet_telemetry_config.return_value = {"response": "ok"}
    w = GarageWorker(_cfg(telemetry=True, telemetry_host="telemetry.example.com"), tesla, aladdin)
    out = w.ensure_telemetry_config()
    assert out["ok"] is True
    tesla.put_fleet_telemetry_config.assert_called_once()
    kwargs = tesla.put_fleet_telemetry_config.call_args.kwargs
    assert kwargs["hostname"] == "telemetry.example.com"
    assert kwargs["port"] == 443
    assert kwargs["minimum_delta"] == 80.0


def test_heartbeat_updates_last_poll_ts():
    import threading
    import time

    tesla, aladdin = _tesla(*HOME), _aladdin()
    w = GarageWorker(_cfg(poll_s=0, telemetry=True), tesla, aladdin)
    assert w.state.last_poll_ts == 0.0

    def stop_soon():
        time.sleep(0.05)
        w._stop.set()

    threading.Thread(target=stop_soon, daemon=True).start()
    w.run_forever()
    tesla.vehicle_location.assert_not_called()
    tesla.put_fleet_telemetry_config.assert_not_called()
    assert w.state.last_poll_ts > 0.0

