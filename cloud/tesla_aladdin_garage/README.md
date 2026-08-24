# Tesla Aladdin garage worker (Cloud Run, always-on, max instances = 1)

Geofence Dhanno → open **Big Peach** via Aladdin Connect.

## Behaviour

- Poll Tesla Fleet `vehicle_data?endpoints=location_data;drive_state` (semicolon).
- **Never** `wake_up` / vehicle commands.
- Home `29.464083,-95.517465`, enter 400 m, hysteresis 80 m (exit 480 m).
- First fix inside the fence does **not** open (already home).
- Cooldown 600 s. `ALADDIN_DRY_RUN=0` is live.
- Single Cloud Run instance, CPU always allocated.

## Env / secrets

GCP identity: [docs/contributing/gcp-access.md](../../docs/contributing/gcp-access.md).
Seed versions with `python3 scripts/secret_manager_put.py` from a laptop/Cloud Shell
where `python3 scripts/gcp_access_probe.py` prints `surface=adc_ready`. Do not put
secrets from a Cursor Cloud Agent, and do not run a second worker locally.

Secrets (Secret Manager → Cloud Run):

- `TESLA_CLIENT_ID` ← `tesla-fleet-client-id`
- `TESLA_CLIENT_SECRET` ← `tesla-fleet-client-secret`
- `TESLA_REFRESH_TOKEN` ← `tesla-fleet-refresh-token` (rotated on refresh)
- `ALADDIN_USERNAME` / `ALADDIN_PASSWORD` ← `aladdin-connect-username` / `aladdin-connect-password`

Public env: `TESLA_VIN`, `TESLA_PARTNER_DOMAIN=yuejj.fleetkey.net`, `HOME_LAT/LON`,
`ALADDIN_DEVICE_SERIAL=F0AD4E3E7403`, `ALADDIN_DOOR_INDEX=1`, `ALADDIN_DRY_RUN=0`.

Re-auth: open `https://<service>/oauth/tesla` (redirect URI must be registered
on the Tesla app).
