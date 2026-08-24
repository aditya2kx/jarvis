# Issue #253 — Finish tesla-aladdin-garage (continue PR #252)

Evidence tier: sandbox-live
scenario: tesla-aladdin-garage-prod

Consulted: `CONTRIBUTING.md` (dev loop + evidence tiers), `docs/contributing/sandbox-evidence.md`, `docs/contributing/prod-changes.md` (feature-flag test: wrong BHAGA numbers? no), `.cursor/rules/bhaga-principles.mdc` breadcrumb + Firestore last-state pattern, `docs/contributing/gcp-access.md` on `origin/cursor/tesla-aladdin-garage-3b01`.

## Jam / §4 (approved in chat 2026-08-24)

Continue **PR #252** (`cursor/tesla-aladdin-garage-3b01`) from this laptop (ADC). Cloud Agent could not seed Secret Manager or deploy. Live **simulate-enter opens Big Peach**. Fetch Tesla location + metres from home. Firestore last-state + skip/open/fail breadcrumbs survive instance death. Stale-poll Cloud Monitoring policy. Runtime-configurable `GEOFENCE_ENTER_M` (Firestore overlay). Admin token required on mutate routes. `Closes #253`. Refresh token empty → `/oauth/tesla` after deploy.

Feature flag: **none** (`docs/contributing/prod-changes.md`). Additive new Cloud Run service; cannot silently produce wrong BHAGA money numbers. Door open is an explicit admin `POST /simulate/enter` plus `ALADDIN_DRY_RUN`. No Operator Console UI (polish N/A).

### Per-scenario evidence (PR §4)

| # | Scenario | Pass criterion |
|---|---|---|
| E1 Happy path — identity | `python3 scripts/gcp_access_probe.py` | `surface=adc_ready` |
| E2 Happy path — secrets | `secret_manager_put.py` from gitignored env (values never printed) | four secrets exist; refresh secret may be placeholder |
| E3 Happy path — units | pytest garage + fixture `vehicle_data` → lat/lon | all pass |
| E4 Happy path — deploy | `workflow_dispatch` `tesla-aladdin-garage-deploy.yml` | describe min=1 max=1 no-cpu-throttling |
| E5 Happy path — location | `GET /location` | JSON lat, lon, `distance_m` now |
| E6 Happy path — simulate open | `POST /simulate/enter` + `X-Garage-Token` | `event=opened`; Aladdin command accepted; log `tesla-aladdin-garage open` |
| E7 Failure — cooldown skip | second simulate inside cooldown | `skip_cooldown` + Cloud Logging `skip reason=cooldown` |
| E8 Recovery — reauth | missing refresh | `/health` `needs_reauth=true`; `/oauth/tesla` then false |
| E9 Config overlay | Firestore `enter_m` e.g. 350 | `/health` shows new radius without image rebuild |
| E10 Pod death | restart revision | Firestore `state` still has last_event / last_poll / last_error |
| E11 Stale poll | Monitoring policy | policy exists; filter greps `tesla-aladdin-garage poll` |
| E12 Legacy | diff vs main | no BHAGA pipeline/console files |

Capture (after deploy; never paste secrets):

```bash
python3 scripts/gcp_access_probe.py
python3 -m pytest -q skills/tesla_fleet/test_client.py skills/aladdin_connect/test_client.py \
  cloud/tesla_aladdin_garage/ scripts/test_gcp_access_probe.py scripts/test_secret_manager_put.py
gcloud run services describe tesla-aladdin-garage --region us-central1 --project jarvis-bhaga-prod
curl -sS "$URL/location"
curl -sS -X POST "$URL/simulate/enter" -H "X-Garage-Token: $GARAGE_ADMIN_TOKEN"
```

### Post-merge verification

```bash
gcloud run services describe tesla-aladdin-garage --region us-central1 --project jarvis-bhaga-prod --format='yaml(status.url,spec.template.metadata.annotations,spec.template.spec.containerConcurrency)'
curl -sS "$URL/health"
curl -sS "$URL/location"
```

## Invariants preserved

- **Single instance** — `--max-instances 1` + `--no-cpu-throttling`; never a second laptop worker.
- **No Tesla wake / vehicle commands** — `vehicle_data` only; semicolon `location_data;drive_state` (`skills/tesla_fleet/client.py` `vehicle_location` ~line 199).
- **First sample never opens** — `Geofence.observe` (`cloud/tesla_aladdin_garage/geofence.py` ~line 36). Simulate must force **outside then enter**.
- **Cooldown** — `GarageWorker._maybe_open` (`worker.py` ~line 136). Do not retry `OPEN_DOOR` on 5xx without operator intent (side-effect guard).
- **Fail-closed admin** — `/tick`, `/simulate/enter`, `/config` return 401 if `GARAGE_ADMIN_TOKEN` missing or mismatch (`app.py` `/tick` currently lines 96–102 allows empty token — **fix**).
- **No secrets in git/chat/PR**. `local/` gitignored.
- BHAGA money/cents/Chicago N/A; additive worker only.

## Docs lock-step

| Change | Doc |
|---|---|
| Deploy / secrets / OAuth / simulate / logs | `RUNBOOK.md` + `cloud/tesla_aladdin_garage/README.md` |
| GCP surfaces | `docs/contributing/gcp-access.md` (already on PR #252) |
| AGENTS table row | `AGENTS.md` (already on PR #252) |
| Checker | `python3 scripts/check_doc_freshness.py --base origin/main` |
| Notable | `PROGRESS.md` via post-merge retro |

## Branch / PR mechanics

- Continue **PR #252** (`cursor/tesla-aladdin-garage-3b01`); merge that branch into this worktree, push to the same PR head. `Closes #253`. `--base main`. Bot `jarvis-agent-bot328`. Never self-merge; babysit. Cost: `pr_cost_ledger.py bind-pr --branch cursor/tesla-aladdin-garage-3b01` then `sync --pr 252`. Reply to Claude thread on #252.

Model routing: Sonnet for all milestones. One chat per PR.

---

## Milestone 1 — Merge #252 + simulate/location/config/state (Sonnet)

### Files

- Merge `origin/cursor/tesla-aladdin-garage-3b01` into `fix/wantt-to-understand-and-work-on`.
- `cloud/tesla_aladdin_garage/geofence.py` — keep `observe` line 36; add `offset_point(lat, lon, dist_m, bearing_deg) -> tuple[float,float]` for simulate.
- `cloud/tesla_aladdin_garage/worker.py`
  - `WorkerConfig.from_env` lines 33–46: already reads `GEOFENCE_ENTER_M`. Add `apply_overlay(d: dict) -> None`.
  - New: `def current_location(self) -> dict` — `tesla.vehicle_location` without `geofence.observe`.
  - New: `def simulate_enter(self) -> str` — observe outside (`enter_m+hysteresis_m+50`), then inside home → `_maybe_open`. Log `tesla-aladdin-garage simulate`.
  - After each tick/simulate: persist Firestore `tesla_aladdin_garage/state` (`last_event`, `last_distance_m`, `last_error`, `last_poll_ts`, `polls`, `opens`, `needs_reauth`).
  - On boot: load overlay from `tesla_aladdin_garage/config`.
- `cloud/tesla_aladdin_garage/persist.py` (new)

```python
def load_config() -> dict: ...
def save_config(d: dict) -> None: ...
def save_state(state) -> None: ...
```

Collection `tesla_aladdin_garage`, docs `config` / `state`. No-op if Firestore client missing (tests mock).

- `cloud/tesla_aladdin_garage/app.py`
  - `_require_admin()` — 401 unless `os.environ["GARAGE_ADMIN_TOKEN"]` non-empty and matches `X-Garage-Token`.
  - `GET /location` — `current_location()` JSON (`ok`, `latitude`, `longitude`, `distance_m`, `shift_state`). No fence mutate.
  - `POST /simulate/enter` — admin; `simulate_enter()`.
  - `POST /config` — admin; body `{"enter_m": 350}` → Firestore + `apply_overlay`; `/health` includes `enter_m`.
  - `/health` also returns `enter_m`, `hysteresis_m`, Firestore last_poll if present.
- `cloud/tesla_aladdin_garage/test_worker.py` — add `test_simulate_enter_opens`, `test_simulate_cooldown`, `test_location_does_not_open`.
- `cloud/tesla_aladdin_garage/test_app.py` (new) — admin 401 without token; 401 wrong token.
- Fixture: `cloud/tesla_aladdin_garage/testdata/vehicle_data.json` + `test_vehicle_location_fixture.py` feeding `TeslaFleetClient.vehicle_location` parse path (extract function or call with mocked `_api`).
- `cloud/tesla_aladdin_garage/requirements.txt` — add `google-cloud-firestore>=2.16,<3`.
- Drop no-op self-rebinds at end of `geofence.py` (Claude nit).

**Verify:** `python3 -m pytest -q cloud/tesla_aladdin_garage/ skills/tesla_fleet/test_client.py skills/aladdin_connect/test_client.py` — all pass.

## Milestone 2 — Secrets, admin token, deploy, alert (Sonnet)

### Files

- `.github/workflows/tesla-aladdin-garage-deploy.yml` (~lines 62–88): add secrets `GARAGE_ADMIN_TOKEN=garage-admin-token:latest`; after deploy, set `TESLA_REDIRECT_URI=$(gcloud run services describe …)/oauth/tesla/callback`. Mount Firestore (default SA already used by other Cloud Run). Grant Cloud Run SA Firestore if needed (`roles/datastore.user` on `bhaga-orchestrator` or the garage SA — use same SA as other services unless workflow specifies `--service-account`).
- `scripts/ensure_garage_stale_poll_alert.py` (new) — idempotent log-based metric `tesla_aladdin_garage_poll` filter `textPayload:"tesla-aladdin-garage poll"` + alert if absent 180s. Call from deploy job. If notification channel missing, still create policy; print policy name (no secrets).
- Laptop (not committed):

```bash
python3 scripts/gcp_access_probe.py   # must print surface=adc_ready
set -a && source local/tesla-aladdin-garage.env && set +a
python3 scripts/secret_manager_put.py --secret tesla-fleet-client-id --from-env TESLA_CLIENT_ID
python3 scripts/secret_manager_put.py --secret tesla-fleet-client-secret --from-env TESLA_CLIENT_SECRET
python3 scripts/secret_manager_put.py --secret aladdin-connect-username --from-env ALADDIN_USERNAME
python3 scripts/secret_manager_put.py --secret aladdin-connect-password --from-env ALADDIN_PASSWORD
# placeholder refresh so --set-secrets succeeds
python3 scripts/secret_manager_put.py --secret tesla-fleet-refresh-token --from-env TESLA_REFRESH_PLACEHOLDER
# generate admin token into env then put garage-admin-token
```

If `TESLA_REFRESH_TOKEN` empty, put a one-line `pending-reauth` placeholder.

- Docs: `cloud/tesla_aladdin_garage/README.md`, `RUNBOOK.md` garage section (simulate, config, logs `grep tesla-aladdin-garage`, Monitoring). `check_doc_freshness.py` coupling if missing.

**Verify:** probe `adc_ready`; `gcloud secrets describe tesla-fleet-client-id --project jarvis-bhaga-prod` (no payload). Deploy workflow green. Describe flags. `curl /location`. `POST /simulate/enter` → opened. Second POST → skip_cooldown.

## Milestone 3 — PR #252 CI + §4 evidence (Sonnet)

- Edit PR body: six sections + `Closes #253` + §4 pasted command output.
- `python3 scripts/pr_cost_ledger.py set-meta --pr 252 --branch cursor/tesla-aladdin-garage-3b01` + `record-build` / `sync`.
- Reply to Claude review comment (evidence + admin token + PII note).
- Push once to `cursor/tesla-aladdin-garage-3b01`. Mark ready (undraft).
- `python3 scripts/verify.py --full` green.
- Babysit: `python3 scripts/pr_triage.py --pr 252`. Do not self-merge.

**Verify:** `python3 scripts/check_pr_description.py --pr 252`; `pr_cost_ledger.py validate --pr 252 --require-build`; CI green.

## Milestone 4 — OAuth if needs_reauth (Sonnet)

If `/health` `needs_reauth`: register Cloud Run callback on Tesla app (Playwright / Tesla developer portal), operator opens `/oauth/tesla` once, then re-curl `/health`.

**Verify:** `needs_reauth=false` and `/location` returns a real fix.
