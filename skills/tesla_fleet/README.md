# Tesla Fleet skill

Official Tesla Fleet API for **location / vehicle data** only. No `wake_up`, no
vehicle commands.

## Auth

| Env | Purpose |
|---|---|
| `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET` | Fleet app (developer.tesla.com) |
| `TESLA_REFRESH_TOKEN` | User token; rotate on refresh |
| `TESLA_PARTNER_DOMAIN` | Public-key host, e.g. `yuejj.fleetkey.net` |
| `TESLA_REDIRECT_URI` | OAuth callback (Cloud Run URL or `http://localhost:8765/oauth/tesla/callback`) |
| `TESLA_AUDIENCE` | Default `https://fleet-api.prd.na.vn.cloud.tesla.com` |

Partner public key must be served at:

`https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem`

## Endpoints gotcha

`GET /api/1/vehicles/{id}/vehicle_data?endpoints=` takes a **semicolon** list
(`location_data;drive_state`). Commas return 400.

## Secrets

Names only: `tesla_fleet_client_id`, `tesla_fleet_client_secret`,
`tesla_fleet_refresh_token` in Secret Manager / env.
