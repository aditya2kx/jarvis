# Tesla Aladdin garage worker (Cloud Run, always-on, max instances = 1)

Geofence Dhanno → open **Big Peach** via Aladdin Connect.

## Behaviour

- Prefer Tesla Fleet Telemetry **push** (HTTP ingest at `POST /telemetry`). REST `vehicle_data` polling is off when `TESLA_TELEMETRY=1` and `POLL_INTERVAL_S=0`.
- Tesla has no geofence webhook. The car (when awake) streams `Location` over mTLS to a self-hosted [fleet-telemetry](https://github.com/teslamotors/fleet-telemetry) host (`TESLA_TELEMETRY_HOST`:`TESLA_TELEMETRY_PORT`, live 8443). Nginx serves the Tesla public key on :443. That process POSTs JSON here. Cloud Run cannot terminate vehicle mTLS.
- `Location.minimum_delta` is 80 m (env `LOCATION_MIN_DELTA_M`). Enter 800 m / hysteresis 80 m.
- Optional REST fallback: set `POLL_INTERVAL_S>0`. `vehicle_data?endpoints=location_data;drive_state` (semicolon).
- **Never** `wake_up` / vehicle commands.
- Home `29.464083,-95.517465`, enter 800 m, hysteresis 80 m (exit 880 m). Override `enter_m` at runtime: `POST /config` `{"enter_m": 350}` (admin token).
- First fix inside the fence does **not** open (already home).
- Cooldown 600 s. `ALADDIN_DRY_RUN=0` is live.
- Single Cloud Run instance, CPU always allocated.
- Last event/error/poll + Tesla usage counters in Firestore **named database `garage`** (`GARAGE_FIRESTORE_DB=garage`), collection `tesla_aladdin_garage/{config,state,tesla_usage}`. Do not use `(default)` on Cloud Run — REST double-encodes it to `%28default%29` (400). Overlay `enter_m` still wins over env.
- Admin token (`GARAGE_ADMIN_TOKEN` / `X-Garage-Token`) required for `/tick`, `/location`, `/simulate/enter`, `/config`, `/telemetry`, `/telemetry/configure`.
- Aladdin `/devices` uses Cognito **AccessToken** (IdToken is 401). Cloud Run SA must have `secretVersionAdder` on `tesla-fleet-refresh-token` so `/oauth/tesla` survives a revision restart.
- If Big Peach is **already open**, skip `OPEN_DOOR` and email `aditya.2ky@gmail.com` (Tesla metres-from-home in the subject). Same email on open and on Aladdin failure. Body includes Tesla Fleet month spend vs the **$10 developer discount** (`TESLA_MONTH_BUDGET_USD`; Jarvis-counted Data/streaming, Tesla portal is authoritative). Needs Gmail OAuth secrets for that mailbox (`GMAIL_*`); without them the worker still opens, it just logs `notify_unconfigured`.

## Env / secrets

GCP identity: [docs/contributing/gcp-access.md](../../docs/contributing/gcp-access.md).
Seed versions with `python3 scripts/secret_manager_put.py` from a laptop/Cloud Shell
where `python3 scripts/gcp_access_probe.py` prints `surface=adc_ready`. Do not put
secrets from a Cursor Cloud Agent, and do not run a second worker locally.

```bash
set -a && source local/tesla-aladdin-garage.env && set +a
python3 scripts/gcp_access_probe.py
python3 scripts/secret_manager_put.py --secret tesla-fleet-client-id --from-env TESLA_CLIENT_ID
python3 scripts/secret_manager_put.py --secret tesla-fleet-client-secret --from-env TESLA_CLIENT_SECRET
python3 scripts/secret_manager_put.py --secret aladdin-connect-username --from-env ALADDIN_USERNAME
python3 scripts/secret_manager_put.py --secret aladdin-connect-password --from-env ALADDIN_PASSWORD
# refresh optional; placeholder lets Cloud Run mount the secret until /oauth/tesla
printf 'pending-reauth' | python3 scripts/secret_manager_put.py --secret tesla-fleet-refresh-token --data-file /dev/stdin
python3 -c "import secrets; print(secrets.token_urlsafe(32))" > /tmp/garage-admin.token
python3 scripts/secret_manager_put.py --secret garage-admin-token --data-file /tmp/garage-admin.token
rm /tmp/garage-admin.token
# Gmail OAuth for aditya.2ky@gmail.com (never Palmetto). Deploy mounts these as GMAIL_*.
python3 scripts/secret_manager_put.py --secret gmail-client-id --from-env GMAIL_CLIENT_ID
python3 scripts/secret_manager_put.py --secret gmail-client-secret --from-env GMAIL_CLIENT_SECRET
python3 scripts/secret_manager_put.py --secret gmail-refresh-token --from-env GMAIL_REFRESH_TOKEN
```

Public env: `TESLA_VIN`, `TESLA_PARTNER_DOMAIN`, `HOME_LAT/LON`, `GEOFENCE_ENTER_M` (800),
`ALADDIN_DEVICE_SERIAL`, `ALADDIN_DOOR_INDEX`, `ALADDIN_DRY_RUN=0`, `GARAGE_PERSIST=1`,
`GARAGE_NOTIFY_TO=aditya.2ky@gmail.com`, `TESLA_TELEMETRY=1`, `POLL_INTERVAL_S=0`,
`TESLA_TELEMETRY_HOST` (fleet-telemetry hostname cars connect to; empty = ingest-only),
`TESLA_TELEMETRY_PORT=8443`,
`LOCATION_MIN_DELTA_M=80`, `TESLA_MONTH_BUDGET_USD=10`, `GARAGE_FIRESTORE_DB=garage`. Gmail OAuth is Secret Manager only (`gmail-client-id`,
`gmail-client-secret`, `gmail-refresh-token` → `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` /
`GMAIL_REFRESH_TOKEN`).

## HTTP

| Path | Auth | Purpose |
|---|---|---|
| `GET /health` | no | polls, last_event, enter_m, needs_reauth, persisted state |
| `GET /location` | admin | live Tesla lat/lon + metres from home (does not open) |
| `POST /simulate/enter` | admin | fake outside→enter then **open Big Peach** |
| `POST /config` | admin | `{"enter_m": 350}` Firestore overlay |
| `POST /telemetry` | admin | fleet-telemetry HTTP-dispatcher JSON → same geofence. Golden samples: `testdata/dispatcher_{outside,enter}.json` (teslamotors PR #91 shape). |
| `POST /telemetry/configure` | admin | `POST /api/1/vehicles/fleet_telemetry_config` if `TESLA_TELEMETRY_HOST` is set |
| `GET /oauth/tesla` | no | operator browser re-auth |

Logs: grep `tesla-aladdin-garage`. Skip reasons: `cooldown`, `no_fix`, `vehicle_unavailable`.
Heartbeat still logs `tesla-aladdin-garage poll` every 20 s when REST poll is off so the stale-poll metric stays valid.
Stale-poll metric: `tesla_aladdin_garage_poll` (deploy job tries to ensure it; IAM miss is non-fatal).

Re-auth: open `https://<service>/oauth/tesla` (callback must be registered on the Tesla app).

Live ingest host: GCE `tesla-fleet-telemetry` (public key `35.239.192.226.sslip.io:443`, vehicle mTLS `:8443`). See `telemetry_host/README.md`.

