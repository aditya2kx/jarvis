"""Subscribe to fleet-telemetry MQTT and POST Location to Cloud Run /telemetry."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

from cloud.tesla_aladdin_garage.mqtt_forwarder import garage_body_from_mqtt

log = logging.getLogger("tesla_aladdin_garage.mqtt_forwarder")


def _post(url: str, token: str, body: dict) -> int:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Garage-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.error("tesla-aladdin-garage fail reason=mqtt_forwarder_missing_paho")
        return 2

    broker = os.environ.get("MQTT_BROKER", "127.0.0.1")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    topic = os.environ.get("MQTT_TOPIC", "telemetry/+/v/Location")
    vin = os.environ.get("TESLA_VIN", "").strip()
    url = os.environ.get("GARAGE_TELEMETRY_URL", "").strip()
    token = os.environ.get("GARAGE_ADMIN_TOKEN", "").strip()
    if not url or not token:
        log.error("tesla-aladdin-garage fail reason=mqtt_forwarder_unconfigured")
        return 2

    def on_connect(client, _userdata, _flags, reason_code, *_rest):
        log.info("tesla-aladdin-garage mqtt_forwarder connected rc=%s", reason_code)
        client.subscribe(topic, qos=1)

    def on_message(_client, _userdata, msg):
        body = garage_body_from_mqtt(msg.topic, msg.payload, expected_vin=vin)
        if not body:
            return
        status = _post(url, token, body)
        if status >= 400:
            log.error(
                "tesla-aladdin-garage fail reason=mqtt_forwarder_post status=%s",
                status,
            )
        else:
            log.info("tesla-aladdin-garage mqtt_forwarder posted status=%s", status)

    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id="garage-mqtt-forwarder",
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id="garage-mqtt-forwarder")
    client.on_connect = on_connect
    client.on_message = on_message
    while True:
        try:
            client.connect(broker, port, keepalive=30)
            client.loop_forever()
        except Exception as e:  # noqa: BLE001
            log.error("tesla-aladdin-garage fail reason=mqtt_forwarder_loop err=%s", e)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
