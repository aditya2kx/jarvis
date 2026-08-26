# Tesla fleet-telemetry GCE host

Always-on `e2-micro` `tesla-fleet-telemetry` in `us-central1-a`. Cars speak mTLS to
`:443`. Official `tesla/fleet-telemetry` has no HTTP dispatcher, so MQTT on
`127.0.0.1:1883` plus `mqtt_forwarder_main.py` POSTs Location to Cloud Run
`/telemetry`.

Hostname: `35.239.192.226.sslip.io` (static external IP). Do not put Caddy/nginx in
front of 443 — fleet-telemetry must terminate vehicle mTLS.

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
