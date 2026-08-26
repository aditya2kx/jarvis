# Tesla fleet-telemetry GCE host

Always-on `e2-micro` `tesla-fleet-telemetry` in `us-central1-a`. Cars speak mTLS to
`:443`. Official `tesla/fleet-telemetry` has no HTTP dispatcher, so MQTT on
`127.0.0.1:1883` plus `mqtt_forwarder_main.py` POSTs Location to Cloud Run
`/telemetry`.

Public CA used in Tesla `fleet_telemetry_config.ca` is Let's Encrypt ISRG Root X1
(Secret Manager `tesla-telemetry-ca`; fetch from https://letsencrypt.org/certs/isrgrootx1.pem).

Tesla `fleet_status` currently reports this VIN as **unpaired** for the Fleet app
(`key_paired=false`). Pair in the Tesla mobile app:
`https://tesla.com/_ak/yuejj.fleetkey.net` (car nearby). Then store the matching
EC private key as Secret Manager `tesla-fleet-command-key` and run
`tesla-http-proxy` on this VM so `POST /api/1/vehicles/fleet_telemetry_config`
can be signed. Unsigned Fleet POST is HTTP 400.

```bash
# After TLS + containers are up (from a laptop with ADC):
gcloud compute ssh tesla-fleet-telemetry --zone us-central1-a --project jarvis-bhaga-prod \
  --tunnel-through-iap --command 'sudo systemctl is-active mosquitto fleet-telemetry garage-mqtt-forwarder'
```
