# Tesla Aladdin garage worker (Cloud Run, always-on, max instances = 1)

Geofence Dhanno → open **Big Peach** via Aladdin Connect.

## Behaviour

- Poll Tesla Fleet `vehicle_data?endpoints=location_data;drive_state` (semicolon).
- **Never** `wake_up` / vehicle commands.
- Home `29.464083,-95.517465`, enter 400 m, hysteresis 80 m (exit 480 m). Override `enter_m` at runtime: `POST /config` `{"enter_m": 350}` (admin token).
- First fix inside the fence does **not** open (already home).
- Cooldown 600 s. `ALADDIN_DRY_RUN=0` is live.
- Single Cloud Run instance, CPU always allocated.
- Last event/error/poll written to Firestore `tesla_aladdin_garage/state` (`GARAGE_PERSIST=1`).
- Admin token (`GARAGE_ADMIN_TOKEN` / `X-Garage-Token`) required for `/tick`, `/location`, `/simulate/enter`, `/config`.
- Aladdin `/devices` uses Cognito **AccessToken** (IdToken is 401). Cloud Run SA must have `secretVersionAdder` on `tesla-fleet-refresh-token` so `/oauth/tesla` survives a revision restart.

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
```

Public env: `TESLA_VIN`, `TESLA_PARTNER_DOMAIN`, `HOME_LAT/LON`, `GEOFENCE_ENTER_M`,
`ALADDIN_DEVICE_SERIAL`, `ALADDIN_DOOR_INDEX`, `ALADDIN_DRY_RUN=0`, `GARAGE_PERSIST=1`.

## HTTP

| Path | Auth | Purpose |
|---|---|---|
| `GET /health` | no | polls, last_event, enter_m, needs_reauth, persisted state |
| `GET /location` | admin | live Tesla lat/lon + metres from home (does not open) |
| `POST /simulate/enter` | admin | fake outside→enter then **open Big Peach** |
| `POST /config` | admin | `{"enter_m": 350}` Firestore overlay |
| `GET /oauth/tesla` | no | operator browser re-auth |

Logs: grep `tesla-aladdin-garage`. Skip reasons: `cooldown`, `no_fix`, `vehicle_unavailable`.
Stale-poll metric: `tesla_aladdin_garage_poll` (deploy job ensures it).

Re-auth: open `https://<service>/oauth/tesla` (callback must be registered on the Tesla app).

