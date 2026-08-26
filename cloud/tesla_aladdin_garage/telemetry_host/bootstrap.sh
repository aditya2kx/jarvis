#!/bin/bash
# Run on tesla-fleet-telemetry as a google-sudoers user.
set -euo pipefail
HOST=35.239.192.226.sslip.io
SRC=/opt/tesla-fleet-telemetry
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq mosquitto python3-paho-mqtt
sudo install -d -m 0755 "$SRC/src/cloud/tesla_aladdin_garage" /etc/mosquitto/conf.d
sudo cp "$SRC/stage/mosquitto.conf" /etc/mosquitto/conf.d/garage.conf
sudo cp "$SRC/stage/server_config.json" "$SRC/server_config.json"
sudo cp "$SRC/stage/fleet-telemetry.service" /etc/systemd/system/fleet-telemetry.service
sudo cp "$SRC/stage/garage-mqtt-forwarder.service" /etc/systemd/system/garage-mqtt-forwarder.service
sudo cp "$SRC/stage/mqtt_forwarder.py" "$SRC/src/cloud/tesla_aladdin_garage/"
sudo cp "$SRC/stage/mqtt_forwarder_main.py" "$SRC/src/cloud/tesla_aladdin_garage/"
sudo cp "$SRC/stage/cloud_init.py" "$SRC/src/cloud/__init__.py"
sudo cp "$SRC/stage/pkg_init.py" "$SRC/src/cloud/tesla_aladdin_garage/__init__.py"
sudo install -m 0600 "$SRC/stage/forwarder.env" "$SRC/forwarder.env"
if [ ! -f /etc/letsencrypt/live/$HOST/fullchain.pem ]; then
  sudo certbot certonly --standalone --non-interactive --agree-tos \
    -m aditya.2ky@gmail.com -d "$HOST"
fi
sudo systemctl enable --now mosquitto
sudo docker pull tesla/fleet-telemetry:latest
sudo systemctl daemon-reload
sudo systemctl enable --now fleet-telemetry garage-mqtt-forwarder
sudo systemctl is-active mosquitto fleet-telemetry garage-mqtt-forwarder
echo BOOTSTRAP_OK
