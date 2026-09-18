# Tesla Aladdin garage worker (Cloud Run, always-on, max instances = 1)

Geofence Dhanno → open **Big Peach** via Aladdin Connect.

## Behaviour

- Prefer Tesla Fleet Telemetry **push** (HTTP ingest at `POST /telemetry`). REST `vehicle_data` polling is off when `TESLA_TELEMETRY=1` and `POLL_INTERVAL_S=0`.
- Tesla has no geofence webhook. The car (when awake) streams `Location` over mTLS to a self-hosted [fleet-telemetry](https://github.com/teslamotors/fleet-telemetry) host (`TESLA_TELEMETRY_HOST`:`TESLA_TELEMETRY_PORT`, live 8443). Nginx serves the Tesla public key on :443. That process POSTs JSON here. Cloud Run cannot terminate vehicle mTLS.
- `Location.minimum_delta` is 40 m (`LOCATION_MIN_DELTA_M`, workflow scope in the deploy YAML). **Deploy-time only** — the Tesla-side push needs the GCE command proxy, so changing it requires a deploy.
- Enter / hysteresis: **300 m / 80 m, exit 380 m**. Runtime config — change with `POST /config`, no deploy.
- Optional REST fallback: set `POLL_INTERVAL_S>0`. `vehicle_data?endpoints=location_data;drive_state` (semicolon).
- **Never** `wake_up` / vehicle commands.
- Home `29.464083,-95.517465`. Radius precedence, highest first: **Firestore** `config.enter_m` (set via `POST /config`) → `cloud/tesla_aladdin_garage/geofence.json` (bootstrap seed) → dataclass default. `GEOFENCE_ENTER_M` env is ignored at every level. `/health` reports `enter_m_source` so the live authority is always visible.
- `POST /config` validates radii (`enter_m` 50–2000 m, `hysteresis_m` 0–500 m → `400 invalid_radii`) and returns `503 config_not_persisted` if the Firestore write fails, so an unpersisted change is never reported as applied.
- First fix inside the fence does **not** open (already home).
- Cooldown 600 s. `ALADDIN_DRY_RUN=0` is live.
- Single Cloud Run instance, CPU always allocated.
- Last event/error/poll + Tesla usage counters in Firestore **named database `garage`** (`GARAGE_FIRESTORE_DB=garage`), collection `tesla_aladdin_garage/{config,state,tesla_usage}`. Do not use `(default)` on Cloud Run — REST double-encodes it to `%28default%29` (400). The `config` doc holds the live radius; the worker loads it on boot and never erases it.
- Admin token (`GARAGE_ADMIN_TOKEN` / `X-Garage-Token`) required for `/tick`, `/location`, `/simulate/enter`, `/config`, `/telemetry`, `/telemetry/configure`.
- Aladdin `/devices` uses Cognito **AccessToken** (IdToken is 401). Cognito access tokens last **24 h**; the client re-logins before expiry and once on a 401, so the always-on Cloud Run instance no longer needs a redeploy to recover after a day. Cloud Run SA must have `secretVersionAdder` on `tesla-fleet-refresh-token` so `/oauth/tesla` survives a revision restart.
- Gmail sends **only from the deployed service** — `send_garage_email` requires `K_SERVICE` (set by Cloud Run) or `GARAGE_NOTIFY_FORCE=1`, so a laptop/CI shell that sourced `local/tesla-aladdin-garage.env` logs `skip reason=notify_not_deployed` instead of mailing the operator. `conftest.py` also strips `GMAIL_*` for every garage test. Both guards exist because the unit suite emailed the operator on every `verify.py --full` (Issue #316): `GarageWorker` defaults `notify` to the live sender, and the open-path tests do not stub it.
- Gmail (`aditya.2ky@gmail.com`) only for a **real** `opened` (Aladdin command sent, not simulated). Already-open, `open_error`, and `/simulate/enter` stay in Cloud Logging (`skip reason=notify_quiet`). Simulate while last live Tesla metres are already ≤ enter_m returns `skip_already_inside` and does **not** open. Body of the remaining open email includes Tesla Fleet month spend vs the **$10 developer discount** (`TESLA_MONTH_BUDGET_USD`; Jarvis-counted Data/streaming, Tesla portal is authoritative). Needs Gmail OAuth secrets for that mailbox (`GMAIL_*`); without them the worker still opens, it just logs `notify_unconfigured`.

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
# Gmail OAuth for aditya.2ky@gmail.com (never Palmetto). Deploy mounts these as GARAGE_GMAIL_*
# (garage-scoped: cloud/pup_watch mails the same operator from the bare GMAIL_* names).
python3 scripts/secret_manager_put.py --secret gmail-client-id --from-env GMAIL_CLIENT_ID
python3 scripts/secret_manager_put.py --secret gmail-client-secret --from-env GMAIL_CLIENT_SECRET
python3 scripts/secret_manager_put.py --secret gmail-refresh-token --from-env GMAIL_REFRESH_TOKEN
```

Public env: `TESLA_VIN`, `TESLA_PARTNER_DOMAIN`, `HOME_LAT/LON` (enter from `geofence.json`, not env),
`ALADDIN_DEVICE_SERIAL`, `ALADDIN_DOOR_INDEX`, `ALADDIN_DRY_RUN=0`, `GARAGE_PERSIST=1`,
`GARAGE_NOTIFY_TO=aditya.2ky@gmail.com`, `TESLA_TELEMETRY=1`, `POLL_INTERVAL_S=0`,
`TESLA_TELEMETRY_HOST` (fleet-telemetry hostname cars connect to; empty = ingest-only),
`TESLA_TELEMETRY_PORT=8443`,
`LOCATION_MIN_DELTA_M=40`, `TESLA_MONTH_BUDGET_USD=10`, `GARAGE_FIRESTORE_DB=garage`. Gmail OAuth is Secret Manager only (`gmail-client-id`,
`gmail-client-secret`, `gmail-refresh-token` → `GARAGE_GMAIL_CLIENT_ID` /
`GARAGE_GMAIL_CLIENT_SECRET` / `GARAGE_GMAIL_REFRESH_TOKEN`). The env names are
service-scoped so pup-watch's bare `GMAIL_*` cannot drive this mailer, and there is no
fallback to them (Issue #316). `GET /health` reports `notify.configured`, `notify.missing` and
`notify.unscoped_present` so a stale rollout is visible without sending mail.

Outgoing mail carries `X-Jarvis-Garage`. The mailbox is shared with `cloud/pup_watch`,
which polls it for `start`/`stop` replies; the marker is how it tells garage mail from a
command (`FOREIGN_MARKER_HEADERS` there). Scoped credentials keep the two from mailing
*as* each other; the marker keeps them from *reading* each other.

Tests can never mail the operator: the root `conftest.py` strips every outbound
credential (including `K_SERVICE` and `GARAGE_NOTIFY_FORCE`, which would otherwise
re-open the runtime gate), on top of this package's own `conftest.py`.

## HTTP

| Path | Auth | Purpose |
|---|---|---|
| `GET /health` | no | polls, last_event, enter_m, `enter_m_source`, needs_reauth, persisted state |
| `GET /location` | admin | live Tesla lat/lon + metres from home (does not open) |
| `POST /simulate/enter` | admin | fake outside→enter then **open Big Peach** (no-op `skip_already_inside` if last Tesla metres already inside; **no Gmail**) |
| `POST /config` | admin | `enter_m` / `hysteresis_m` / `cooldown_s` / `poll_s`; applies immediately and persists. `400 invalid_radii` out of bounds, `503 config_not_persisted` if Firestore write fails |
| `POST /telemetry` | admin | fleet-telemetry HTTP-dispatcher JSON → same geofence. Golden samples: `testdata/dispatcher_{outside,enter}.json` (teslamotors PR #91 shape). |
| `POST /telemetry/configure` | admin | skip unless `TESLA_COMMAND_PROXY_URL` (Cloud Run: use GCE `gce_signed_telemetry_config`) |
| `GET /oauth/tesla` | no | operator browser re-auth |

Logs: grep `tesla-aladdin-garage`. Skip reasons: `cooldown`, `no_fix`, `vehicle_unavailable`, `telemetry_config_needs_proxy`, `notify_quiet`, `notify_not_deployed`, `simulate_already_inside`.
Heartbeat still logs `tesla-aladdin-garage poll` every 20 s when REST poll is off so the stale-poll metric stays valid.
Stale-poll metric: `tesla_aladdin_garage_poll` (deploy job tries to ensure it; IAM miss is non-fatal).

Re-auth: open `https://<service>/oauth/tesla` (callback must be registered on the Tesla app).

Live ingest host: GCE `tesla-fleet-telemetry` (public key `35.239.192.226.sslip.io:443`, vehicle mTLS `:8443`). See `telemetry_host/README.md`.

