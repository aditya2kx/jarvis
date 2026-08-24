"""Cloud Run entry: /health, Tesla OAuth, background geofence worker.

Always-on: min instances = 1, CPU never throttled, max instances = 1.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from flask import Flask, jsonify, redirect, request

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


@app.get("/health")
def health():
    w = get_worker()
    st = w.state
    return jsonify(
        {
            "ok": True,
            "service": "tesla-aladdin-garage",
            "vin": w.cfg.vin,
            "dry_run": w.cfg.dry_run,
            "needs_reauth": st.needs_reauth or w.tesla.needs_user_auth(),
            "last_event": st.last_event,
            "last_distance_m": st.last_distance_m,
            "polls": st.polls,
            "opens": st.opens,
            "last_error": st.last_error,
            "partner_domain": w.tesla.partner_domain,
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
    log.info("tesla-aladdin-garage oauth_ok")
    return jsonify({"ok": True, "message": "Tesla tokens stored; next poll will use them."})


@app.post("/tick")
def tick():
    token = os.environ.get("GARAGE_ADMIN_TOKEN", "")
    if token and request.headers.get("X-Garage-Token") != token:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    event = get_worker().tick()
    return jsonify({"ok": True, "event": event})


def start_background() -> None:
    t = threading.Thread(target=get_worker().run_forever, name="geofence", daemon=True)
    t.start()


if os.environ.get("GARAGE_WORKER", "1") != "0":
    try:
        start_background()
    except Exception as e:
        log.error("tesla-aladdin-garage fail reason=worker_boot err=%s", e)
