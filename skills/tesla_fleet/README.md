# Tesla Fleet skill

Official Tesla Fleet API for **location / vehicle data** only. No `wake_up`, no
vehicle commands.

## Auth

| Env | Purpose |
|---|---|
| `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET` | Fleet app (developer.tesla.com) |
| `TESLA_REFRESH_TOKEN` | User token; rotate on refresh |
| `TESLA_PARTNER_DOMAIN` | Public-key host, e.g. `35.239.192.226.sslip.io` |
| `TESLA_REDIRECT_URI` | OAuth callback (Cloud Run URL or `http://localhost:8765/oauth/tesla/callback`) |
| `TESLA_AUDIENCE` | Default `https://fleet-api.prd.na.vn.cloud.tesla.com` |

Partner public key must be served at:

`https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem`

## Endpoints gotcha

`GET /api/1/vehicles/{id}/vehicle_data?endpoints=` takes a **semicolon** list
(`location_data;drive_state`). Commas return 400.

## Fleet Telemetry config

`POST /api/1/vehicles/fleet_telemetry_config` asks Tesla to stream `Location` to a
self-hosted [fleet-telemetry](https://github.com/teslamotors/fleet-telemetry)
hostname:port (mTLS; garage uses 8443 so :443 can serve the Tesla public key). Fields use `interval_seconds` + `minimum_delta` (metres).
This is **not** a geofence webhook and does **not** wake a sleeping vehicle.
`fleet_telemetry_config_body()` / `put_fleet_telemetry_config()` build that POST.
Tesla requires the call through the Vehicle Command HTTP Proxy (`TESLA_COMMAND_PROXY_URL`,
GCE `tesla-http-proxy` on `https://127.0.0.1:4443`). Unsigned Fleet API is HTTP 400.
Cloud Run never POSTs unsigned; laptop/deploy uses
`python3 -m cloud.tesla_aladdin_garage.gce_signed_telemetry_config` (IAP SSH onto the VM).
The garage worker consumes the HTTP dispatcher JSON at `POST /telemetry`.
Garage notify emails include Tesla Fleet month-to-date vs Tesla's **$10/mo
developer discount** (`TESLA_MONTH_BUDGET_USD`). Tesla has no billing API; we count
our own status<500 Fleet calls plus ingested Location signals.

## Secrets

Names only: `tesla_fleet_client_id`, `tesla_fleet_client_secret`,
`tesla_fleet_refresh_token` in Secret Manager / env.
