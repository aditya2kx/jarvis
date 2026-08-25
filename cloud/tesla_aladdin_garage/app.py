"""Cloud Run entry: /health, Tesla OAuth, location, simulate-enter, geofence worker.

Always-on: min instances = 1, CPU never throttled, max instances = 1.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from flask import Flask, jsonify, redirect, request

from cloud.tesla_aladdin_garage import persist
from cloud.tesla_aladdin_garage.telemetry import extract_location_fixes
from cloud.tesla_aladdin_garage.worker import GarageWorker, WorkerConfig
from skills.aladdin_connect.client import AladdinConnectClient
from skills.tesla_fleet.client import TeslaFleetClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("tesla_aladdin_garage.app")

app = Flask(__name__)
_worker: Optional[GarageWorker] = None
_lock = threading.Lock()


def _persist_tokens(tokens: dict) -> None:
    name = os.environ.get("TESLA_REFRESH_TOKEN_SECRET", "tesla-fleet-refresh-token")
    project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    refresh = tokens.get("refresh_token")
    if not refresh or not project:
        return
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{project}/secrets/{name}"
        client.add_secret_version(
            request={"parent": parent, "payload": {"data": refresh.encode("utf-8")}}
        )
        log.info("tesla-aladdin-garage token_persist secret=%s", name)
    except Exception as e:
        log.error("tesla-aladdin-garage fail reason=token_persist err=%s", e)


def get_worker() -> GarageWorker:
    global _worker
    with _lock:
        if _worker is None:
            tesla = TeslaFleetClient.from_env(on_tokens=_persist_tokens)
            aladdin = AladdinConnectClient.from_env()
            _worker = GarageWorker(WorkerConfig.from_env(), tesla, aladdin)
        return _worker


def _require_admin():
    expected = os.environ.get("GARAGE_ADMIN_TOKEN", "")
    got = request.headers.get("X-Garage-Token", "")
    if not expected or got != expected:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


@app.get("/health")
def health():
    w = get_worker()
    st = w.state
    stored = persist.load_state()
    return jsonify(
        {
            "ok": True,
            "service": "tesla-aladdin-garage",
            "vin": w.cfg.vin,
            "dry_run": w.cfg.dry_run,
            "enter_m": w.cfg.enter_m,
            "hysteresis_m": w.cfg.hysteresis_m,
            "needs_reauth": st.needs_reauth or w.tesla.needs_user_auth(),
            "last_event": st.last_event,
            "last_distance_m": st.last_distance_m,
            "polls": st.polls,
            "opens": st.opens,
            "last_error": st.last_error,
            "partner_domain": w.tesla.partner_domain,
            "persisted": stored,
            "telemetry": w.cfg.telemetry,
            "poll_s": w.cfg.poll_s,
        }
    )


@app.get("/location")
def location():
    denied = _require_admin()
    if denied:
        return denied
    return jsonify(get_worker().current_location())


@app.post("/simulate/enter")
def simulate_enter():
    denied = _require_admin()
    if denied:
        return denied
    event = get_worker().simulate_enter()
    return jsonify({"ok": True, "event": event})


@app.post("/config")
def config():
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    overlay = {k: body[k] for k in ("enter_m", "hysteresis_m", "cooldown_s", "poll_s") if k in body}
    persist.save_config(overlay)
    get_worker().apply_overlay(overlay)
    w = get_worker()
    return jsonify(
        {
            "ok": True,
            "enter_m": w.cfg.enter_m,
            "hysteresis_m": w.cfg.hysteresis_m,
            "cooldown_s": w.cfg.cooldown_s,
            "poll_s": w.cfg.poll_s,
        }
    )


@app.get("/oauth/tesla")
def oauth_start():
    return redirect(get_worker().tesla.authorization_url(), code=302)


@app.get("/oauth/tesla/callback")
def oauth_callback():
    tesla = get_worker().tesla
    err = request.args.get("error")
    if err:
        return jsonify({"ok": False, "error": err}), 400
    tesla.exchange_code(request.args.get("code") or "", state=request.args.get("state") or "")
    w = get_worker()
    w.state.needs_reauth = False
    w.state.last_error = ""
    cfg = w.ensure_telemetry_config()
    log.info("tesla-aladdin-garage oauth_ok telemetry_config=%s", cfg.get("reason") or cfg.get("ok"))
    return jsonify(
        {
            "ok": True,
            "message": "Tesla tokens stored; telemetry config attempted.",
            "telemetry_config": cfg,
        }
    )


@app.post("/tick")
def tick():
    denied = _require_admin()
    if denied:
        return denied
    event = get_worker().tick()
    return jsonify({"ok": True, "event": event})


@app.post("/telemetry")
def telemetry():
    """Ingest fleet-telemetry HTTP-dispatcher JSON. Never wakes the car."""
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"ok": False, "error": "json_required"}), 400
    w = get_worker()
    fixes = extract_location_fixes(body, expected_vin=w.cfg.vin)
    if not fixes:
        log.info("tesla-aladdin-garage skip reason=telemetry_no_fix")
        return jsonify({"ok": True, "event": "skip_no_fix", "fixes": 0})
    persist.record_billable("signals", len(fixes))
    events = [
        w.observe_fix(f["latitude"], f["longitude"], f.get("shift_state"), source="telemetry")
        for f in fixes
    ]
    return jsonify({"ok": True, "event": events[-1], "events": events, "fixes": len(fixes)})


@app.post("/telemetry/configure")
def telemetry_configure():
    denied = _require_admin()
    if denied:
        return denied
    return jsonify(get_worker().ensure_telemetry_config())


def start_background() -> None:
    t = threading.Thread(target=get_worker().run_forever, name="geofence", daemon=True)
    t.start()


if os.environ.get("GARAGE_WORKER", "1") != "0":
    try:
        start_background()
    except Exception as e:
        log.error("tesla-aladdin-garage fail reason=worker_boot err=%s", e)
