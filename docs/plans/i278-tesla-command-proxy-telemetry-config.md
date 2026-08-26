# Issue #278 — Signed `fleet_telemetry_config` via Tesla Vehicle Command Proxy

Evidence tier: sandbox-e2e
scenario: tesla-aladdin-garage-prod

Consulted: `CONTRIBUTING.md` (dev loop + §4), `docs/contributing/sandbox-evidence.md`, `docs/contributing/prod-changes.md` (flag test: cannot silently change BHAGA money → **no flag**), `.cursor/rules/bhaga-principles.mdc` breadcrumb, `RUNBOOK.md` Tesla Aladdin (~line 1987), `skills/tesla_fleet/README.md` lines 25–33, `cloud/tesla_aladdin_garage/telemetry_host/README.md` lines 11–13. Jam + §4 approved in chat 2026-08-26 with sequencing: **Tesla signed path must return HTTP 200 before we remove the unsigned Cloud Run POST.**

## Jam / §4 (approved in chat 2026-08-26)

Keys are healthy. Tesla-app push is not Jarvis. Cloud Run `ensure_telemetry_config` POSTs unsigned Fleet `fleet_telemetry_config` → Tesla 400 (must use Vehicle Command HTTP Proxy). `tesla-http-proxy` is already **active** on GCE `tesla-fleet-telemetry` (`127.0.0.1:4443`).

**Sequencing (operator):** get the Tesla proxy POST working (HTTP 200) **before** deprecating/removing the current unsigned Cloud Run POST. Do not skip unsigned first.

Feature flag: **none**. Additive command-path routing; geofence/Aladdin/BHAGA numbers unchanged. No Operator Console UI (polish N/A).

### Per-scenario evidence (PR §4)

| # | Scenario | Pass criterion |
|---|---|---|
| E1 Happy — units | pytest `skills/tesla_fleet/` + `cloud/tesla_aladdin_garage/` | proxy URL used when set; unsigned still callable when unset (until M3 tests flip) |
| E2 Happy — Tesla signed | IAP SSH or tunnel + `TeslaFleetClient.put_fleet_telemetry_config` via `https://127.0.0.1:4443` | Tesla **200**; body has `updated_vehicles` or equivalent; **no** “must be called through the Vehicle Command HTTP Proxy” |
| E3 Dual-run | After E2, Cloud Run **still** has the unsigned boot POST until M3 lands | logs may still show one 400 from old revision; streaming `source=telemetry` still live |
| E4 Cutover | M3 deploy: worker skip or proxy-only | **no** new `fail reason=telemetry_config status=400`; skip `telemetry_config_needs_proxy` **or** signed 200 |
| E5 Failure — no proxy | `TESLA_COMMAND_PROXY_URL` empty after cutover | skip INFO, not unsigned POST; stream unchanged |
| E6 Recovery — proxy down | proxy URL set but connection fails | breadcrumb `fail reason=telemetry_config`; **do not** fall back to unsigned after M3 |
| E7 Legacy | `git diff origin/main` | garage + tesla_fleet + RUNBOOK/README + deploy yml only; no BHAGA pipeline/console |

### Post-merge verification

```bash
curl -sS "$URL/health"
gcloud logging read 'resource.labels.service_name="tesla-aladdin-garage" AND textPayload:"fleet_telemetry_config" AND timestamp>="'"$(date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)"'"' --project=jarvis-bhaga-prod --limit=10
gcloud compute ssh tesla-fleet-telemetry --zone us-central1-a --project jarvis-bhaga-prod --tunnel-through-iap --command 'sudo systemctl is-active tesla-http-proxy'
```

## Invariants preserved

- **No wake_up / vehicle drive commands** — only signed `fleet_telemetry_config` (Location stream registration). `skills/tesla_fleet/client.py` `put_fleet_telemetry_config` ~line 266.
- **Single Cloud Run instance** — deploy.yml `--max-instances 1` / `--no-cpu-throttling` unchanged.
- **Do not open Big Peach** in this PR (`POST /simulate/enter` out of scope).
- **Unsigned remains until signed 200 is captured** (operator sequencing).
- After cutover: **no unsigned fallback** (would recreate the 400 + bill a command).
- **No secrets in git/PR.** Proxy TLS is localhost-only (`127.0.0.1:4443`); do not expose nginx `/command-proxy` on :443 in this PR.
- BHAGA cents/Chicago N/A.

## Docs lock-step

| Change | Doc |
|---|---|
| Proxy vs unsigned; re-subscribe recipe | `RUNBOOK.md` Tesla Aladdin (~line 1980) + `cloud/tesla_aladdin_garage/README.md` + `telemetry_host/README.md` + `skills/tesla_fleet/README.md` |
| Deploy GCE configure step | `RUNBOOK.md` + `tesla-aladdin-garage-deploy.yml` |
| Checker | `python3 scripts/check_doc_freshness.py --base origin/main` |

## Branch / PR mechanics

Branch `fix/saw-this-phone-notification-from-tesla`. `gh pr create --base main`. `Closes #278`. Bot `jarvis-agent-bot328`. Never self-merge; babysit. Cost: `bind-pr` then `sync`. One chat per PR.

Model routing (`docs/contributing/cost.md`): M1 Sonnet (client + live signed POST) · M2 Sonnet (deploy YAML) · M3 Sonnet (cutover + docs). No Opus.

---

## Milestone 1 — Tesla proxy client + live signed 200 (keep unsigned)

### Files

- `skills/tesla_fleet/client.py` `TeslaFleetClient.__init__` ~line 110: add `command_proxy_url: str = ""`. `from_env` ~line 140: `TESLA_COMMAND_PROXY_URL`.
- `put_fleet_telemetry_config` ~line 266: POST to `(command_proxy_url or audience) + /api/1/vehicles/fleet_telemetry_config`. When proxy URL is set, urllib SSL must accept the proxy’s localhost TLS cert (`ssl._create_unverified_context` **only** for that host, or load `/opt/tesla-fleet-telemetry/proxy/tls-cert.pem` when present).
- `_http_json` ~line 343: optional `ssl_context`.
- `skills/tesla_fleet/test_client.py`: assert proxy base used; unsigned audience still used when URL empty.
- `cloud/tesla_aladdin_garage/telemetry_host/configure_fleet_telemetry.py` (new): `from_env()` + `put_fleet_telemetry_config` with host `35.239.192.226.sslip.io` port 8443 CA from `TESLA_TELEMETRY_CA`. Intended to run **on the VM** or laptop with IAP tunnel to `:4443`. Never prints tokens.

Do **not** change `GarageWorker.ensure_telemetry_config` (`worker.py` ~line 226) or `run_forever` ~line 397 in this milestone.

### Verify

```bash
python3 -m pytest -q skills/tesla_fleet/test_client.py cloud/tesla_aladdin_garage/test_worker.py
# Live signed (ADC + IAP). Capture status only; no token in output:
gcloud compute start-iap-tunnel tesla-fleet-telemetry 4443 --local-host-port=localhost:4443 --zone us-central1-a --project jarvis-bhaga-prod
# other shell: TESLA_COMMAND_PROXY_URL=https://127.0.0.1:4443 python3 -m cloud.tesla_aladdin_garage.telemetry_host.configure_fleet_telemetry
```

Pass: pytest green; Tesla **200** on signed POST; Cloud Run worker still unsigned (unchanged).

---

## Milestone 2 — Dual-run: deploy runs signed configure on GCE

### Files

- `.github/workflows/tesla-aladdin-garage-deploy.yml` after Cloud Run update (~line 96): new step **IAP SSH** `tesla-fleet-telemetry` running `configure_fleet_telemetry.py` with `TESLA_COMMAND_PROXY_URL=https://127.0.0.1:4443`. `continue-on-error: true` if IAM/IAP miss must not fail Cloud Run (same pattern as stale-poll ~line 97). Prefer: fetch secrets on the VM via ADC/SA already on the box, or `gcloud secrets versions access` in the GHA job and pass env through ssh (do not echo).
- `cloud/tesla_aladdin_garage/test_deploy_workflow.py`: assert step name + `TESLA_COMMAND_PROXY_URL` + IAP ssh present.
- `telemetry_host/README.md` + RUNBOOK: signed configure is canonical; unsigned Cloud Run still exists until M3.

### Verify

```bash
python3 -m pytest -q cloud/tesla_aladdin_garage/test_deploy_workflow.py
```

Pass: YAML contains GCE signed configure; M1 live 200 already recorded. Unsigned Cloud Run POST **still in** `worker.py` `run_forever`.

---

## Milestone 3 — Remove unsigned Cloud Run POST + docs

**Only after M1 live 200 is in the evidence log.**

### Files

- `cloud/tesla_aladdin_garage/worker.py` `ensure_telemetry_config` ~line 226: if no `command_proxy_url` on the Tesla client, log `tesla-aladdin-garage skip reason=telemetry_config_needs_proxy` INFO and **return without** `put_fleet_telemetry_config`. If proxy URL set, call it (Cloud Run will not set it this PR — localhost only).
- `test_worker.py` `test_ensure_telemetry_config_posts_when_host_set` ~line 196: split into skip-without-proxy vs post-with-proxy.
- `app.py` `/telemetry/configure` ~line 191 and oauth callback ~line 149: same skip (via `ensure_telemetry_config`).
- Docs: `RUNBOOK.md`, `cloud/tesla_aladdin_garage/README.md`, `skills/tesla_fleet/README.md`, `telemetry_host/README.md`.
- `python3 scripts/check_doc_freshness.py --base origin/main`

### Verify

```bash
python3 -m pytest -q skills/tesla_fleet/test_client.py cloud/tesla_aladdin_garage/
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
```

Pass: no unsigned `put` from worker when proxy URL empty; docs state GCE proxy is the configure path.

---

## Code stubs

```python
def put_fleet_telemetry_config(self, *, vins: list[str], hostname: str, port: int = 443, ca: str = "", interval_seconds: int = 15, minimum_delta: float = 80.0) -> dict:
    body = fleet_telemetry_config_body(...)
    base = (self.command_proxy_url or self.audience).rstrip("/")
    return self._api("POST", "/api/1/vehicles/fleet_telemetry_config", payload=json.dumps(body).encode(), base_url=base, ssl_insecure=bool(self.command_proxy_url))
```

Env: `TESLA_COMMAND_PROXY_URL` (empty on Cloud Run). GCE: `https://127.0.0.1:4443`.
