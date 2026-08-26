# Tesla fleet-telemetry GCE host

Always-on `e2-micro` `tesla-fleet-telemetry` in `us-central1-a`. Cars speak mTLS to
`:443`. Official `tesla/fleet-telemetry` has no HTTP dispatcher, so MQTT on
`127.0.0.1:1883` plus `mqtt_forwarder_main.py` POSTs Location to Cloud Run
`/telemetry`.

Public CA used in Tesla `fleet_telemetry_config.ca` is Let's Encrypt ISRG Root X1
(Secret Manager `tesla-telemetry-ca`; fetch from https://letsencrypt.org/certs/isrgrootx1.pem).

Tesla `fleet_status` reports this VIN unpaired until the Tesla app enrolls
`https://tesla.com/_ak/h1hh2.fleetkey.net` (car nearby). Command private key is
Secret Manager `tesla-fleet-command-key`; `tesla-http-proxy` on this VM (`127.0.0.1:4443`)
signs `fleet_telemetry_config`. Unsigned Fleet POST is HTTP 400.

```bash
# After TLS + containers are up (from a laptop with ADC):
gcloud compute ssh tesla-fleet-telemetry --zone us-central1-a --project jarvis-bhaga-prod \
  --tunnel-through-iap --command 'sudo systemctl is-active mosquitto fleet-telemetry garage-mqtt-forwarder'
```
