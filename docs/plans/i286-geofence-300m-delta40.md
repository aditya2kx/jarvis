# Issue #286 — Garage radius as runtime config (set to 300 m), Location delta 80 → 40 m

Consulted: `CONTRIBUTING.md` (dev loop + evidence), `docs/plans/i280-geofence-sot-500m.md` (the decision this PR
revises), `PROGRESS.md:1-5` (the 2026-08-27 entry), `RUNBOOK.md` line 1931 + lines 1938–1947,
`cloud/tesla_aladdin_garage/README.md` lines 9, 12, 51 and 62,
`cloud/tesla_aladdin_garage/worker.py` lines 26, 44–61, 64–69, 104–123,
`cloud/tesla_aladdin_garage/persist.py` lines 79–108, `cloud/tesla_aladdin_garage/app.py` lines 69–133,
`cloud/tesla_aladdin_garage/geofence.py` lines 14–21 and 37,
`cloud/tesla_aladdin_garage/gce_signed_telemetry_config.py` lines 51–58,
`.github/workflows/tesla-aladdin-garage-deploy.yml` lines 13–18, 77, 96–100,
`.cursor/rules/jarvis.mdc` (config-driven, no hardcoding), `.cursor/rules/doc-maintenance.mdc` (`cloud/**` → RUNBOOK).

Jam + §4 approved in chat 2026-09-13, then **revised in the same chat** on operator feedback:
*"I thought this would be a config change and not a code deploy. make it such for future."*

Scope as revised:
1. The enter radius becomes **runtime config** — changeable via `POST /config` with no deploy, no image rebuild.
2. Using that mechanism, the effective radius becomes **300 m** (hysteresis unchanged at 80 m → exit 380 m).
3. `LOCATION_MIN_DELTA_M` 80 → 40 m stays a **deploy-time** change (see "Why the delta cannot be runtime config").
4. Live `POST /simulate/enter` remains **excluded** from the evidence pack per the operator.

Evidence tier: unit-only
waiver: the garage worker has no sandbox — BHAGA's harness does not cover it, and the only live geofence
proof would be `POST /simulate/enter`, which physically opens Big Peach and which the operator excluded.
Prod is verified post-merge by read-only `GET /health`, by the `POST /config` round-trip (the feature
itself), and by the deploy log for the signed telemetry-config step.

Feature flag: **none**. The radius *is* the product change and cannot silently produce wrong BHAGA numbers
(no money path, no sheet, no ledger). No Operator Console UI, so the UX-polish bar is N/A.

Prod baseline captured during jam (`GET /health`, 2026-09-13):
`enter_m=500.0 hysteresis_m=80.0 last_event=inside last_distance_m=37.98 opens=2 telemetry=true polls=3576`.

## Why this reverses part of Issue #280 — and how it keeps that lesson

#280 made `geofence.json` the sole writer after a stale Firestore overlay (200 m) silently pinned the
radius while the operator believed it was 800 m. The root cause was **three writers with undefined
precedence** (env, git file, Firestore overlay), not the existence of a runtime knob. #280 fixed it by
deleting the knob, which is why changing a threshold now costs a full image build and Cloud Run rollout.

This PR keeps "exactly one authority" but makes that authority *runtime*:

| Source | Before (#280) | After (this PR) |
|---|---|---|
| `GEOFENCE_ENTER_M` env | ignored | ignored (unchanged) |
| Firestore `config.enter_m` | actively deleted on boot | **authoritative when present** |
| `geofence.json` | sole SoT | bootstrap seed — used only when Firestore has no value |

Three guards make the #280 incident non-repeatable:

- `GET /health` reports `enter_m_source` (`firestore` or `geofence.json`), so "which value is live and why"
  is always answerable without reading code. Boot logs the same.
- `POST /config` validates and echoes the effective value, and returns **503** when the Firestore write did
  not persist — the old `save_config` swallowed failures, which would let an operator believe a change
  landed when a restart would silently revert it.
- Bounds validation rejects fat-fingered radii (`enter_m` outside 50–2000 m, `hysteresis_m` outside
  0–500 m) with 400 rather than accepting a value that never opens the door or opens it from the office.

`clear_geofence_overlay()` (`persist.py:79`) is deleted: its only purpose was enforcing the file-only
regime this PR replaces, and leaving it would erase the operator's config on every restart.

## Why the delta cannot be runtime config

`LOCATION_MIN_DELTA_M` has two consumers and only one changes what the car does:

- `worker.py:61` → `WorkerConfig.location_delta_m`, but `ensure_telemetry_config()` short-circuits with
  `telemetry_config_needs_proxy` in prod (no `TESLA_COMMAND_PROXY_URL` on Cloud Run).
- `gce_signed_telemetry_config.py:57` builds the body actually POSTed to Tesla, reading the **GitHub Actions
  runner env** — and the step at `.github/workflows/tesla-aladdin-garage-deploy.yml:96` has no `env:` block,
  so the variable is unset there and the `or 80` fallback wins.

Editing only the `--set-env-vars` string on line 77 would be a silent no-op: Cloud Run would report 40 while
the car kept publishing every 80 m. Tesla-side telemetry config requires a signed command-proxy call that
Cloud Run cannot make, so this value stays deploy-time. M2 fixes the wiring so the deploy actually applies it.

## Milestone 1 — Radius becomes runtime config (Sonnet)

### Files

`cloud/tesla_aladdin_garage/worker.py:64-69` — `WorkerConfig.apply_overlay` currently skips the two keys.
Accept them, validated:

```python
ENTER_M_BOUNDS = (50.0, 2000.0)
HYSTERESIS_M_BOUNDS = (0.0, 500.0)


def validate_radii(enter_m: float, hysteresis_m: float) -> None:
    """Raise ValueError when a radius would make the fence useless or absurd."""
```

`apply_overlay` applies `enter_m` / `hysteresis_m` when present and valid, alongside the existing
`cooldown_s` / `poll_s`. `GarageWorker.apply_overlay` (`worker.py:114-117`) already re-points the live
`Geofence`, so no change is needed there.

`cloud/tesla_aladdin_garage/worker.py:107` — delete the `persist.clear_geofence_overlay()` call so a restart
no longer erases operator config. Record `self.cfg.enter_m_source` (`"firestore"` when the loaded overlay
carried `enter_m`, else `"geofence.json"`) and log it in the existing config log line at `worker.py:118`.

`cloud/tesla_aladdin_garage/persist.py:79-97` — delete `clear_geofence_overlay()` entirely.

`cloud/tesla_aladdin_garage/persist.py:100-107` — `save_config` stops filtering the geofence keys and starts
reporting success:

```python
def save_config(overlay: dict) -> bool:
    """Persist overlay to Firestore. Returns False when the write did not land."""
```

Return `False` when `_db()` is `None` or the write raises (currently it lets exceptions escape and silently
no-ops when persistence is off).

`cloud/tesla_aladdin_garage/app.py:113-133` — `POST /config` replaces the 409 at line 119 with:
`400` + `{"ok": false, "error": "invalid_radii", "detail": …}` on `ValueError` from `validate_radii`;
`503` + `{"ok": false, "error": "config_not_persisted"}` when `save_config` returns `False`; otherwise apply
and echo the effective config.

`cloud/tesla_aladdin_garage/app.py:80` — add `"enter_m_source": w.cfg.enter_m_source` to the `/health` body.

### Tests

`cloud/tesla_aladdin_garage/test_worker.py:171-176` — `test_overlay_cannot_change_enter_m` inverts into
`test_overlay_sets_enter_m`: overlay `{"enter_m": 350}` moves both `w.cfg.enter_m` and `w.geofence.enter_m`
to 350. Keep `test_from_env_reads_json_not_geofence_env` (`test_worker.py:181`) — env is still ignored.

`cloud/tesla_aladdin_garage/test_app.py:40-45` — `test_config_rejects_enter_m` becomes
`test_config_accepts_enter_m` (200 + echoed value), plus new `test_config_rejects_out_of_bounds_enter_m`
(400) and `test_config_503_when_not_persisted` (`save_config` monkeypatched to return `False`).

`cloud/tesla_aladdin_garage/test_persist.py:70` — drop the `clear_geofence_overlay` DELETE_FIELD assertion;
add `test_save_config_keeps_enter_m` proving the key now reaches the payload.

New `test_worker.py::test_boot_prefers_firestore_over_file` — with a fake overlay carrying `enter_m`, boot
reports `enter_m_source == "firestore"`; with an empty overlay, `"geofence.json"`.

### Verify

```bash
python3 -m pytest -q cloud/tesla_aladdin_garage/test_worker.py \
  cloud/tesla_aladdin_garage/test_app.py \
  cloud/tesla_aladdin_garage/test_persist.py
```

Pass: all green; overlay sets 350; out-of-bounds rejected; unpersisted write surfaces 503.

## Milestone 2 — Seed 300 m and fix the delta wiring (Sonnet)

### Files

`cloud/tesla_aladdin_garage/geofence.json` — the bootstrap seed, kept in step with the intended value so a
Firestore wipe recovers to 300 rather than 500:

```json
{
  "enter_m": 300,
  "hysteresis_m": 80
}
```

- `cloud/tesla_aladdin_garage/geofence.py:37` — `enter_m: float = 500.0` → `300.0`
- `cloud/tesla_aladdin_garage/worker.py:26` — `enter_m: float = 500.0` → `300.0`
- `cloud/tesla_aladdin_garage/test_geofence.py:12` — `assert enter_m == 500` → `== 300`
- `cloud/tesla_aladdin_garage/test_worker.py:187` — `assert cfg.enter_m == 500` → `== 300`

Do **not** touch tests that construct `Geofence(*HOME, enter_m=400, ...)` (`test_geofence.py:25,31,37`) or
`test_notify.py:9,14` — those pin their own radii.

`.github/workflows/tesla-aladdin-garage-deploy.yml:13-18` — one key at workflow scope so both consumers read
the same value:

```yaml
env:
  PROJECT_ID: jarvis-bhaga-prod
  REGION: us-central1
  REGISTRY: us-central1-docker.pkg.dev/jarvis-bhaga-prod/jarvis-images
  SERVICE: tesla-aladdin-garage
  LOCATION_MIN_DELTA_M: "40"
```

Line 77 — inside `--set-env-vars`, `LOCATION_MIN_DELTA_M=80` → `LOCATION_MIN_DELTA_M=${{ env.LOCATION_MIN_DELTA_M }}`.
Every other key in that string stays untouched. A workflow-level `env:` key is visible to the `run:` step at
line 96–100, which is what makes the Tesla-side push honour 40.

Leave the `80.0` defaults at `worker.py:38`, `worker.py:61`, `gce_signed_telemetry_config.py:57` and
`skills/tesla_fleet/client.py:47,280` alone — `skills/tesla_fleet/` is generic and must not carry
garage-specific tuning (`.cursor/rules/jarvis.mdc` § Skills ≠ agents).

`cloud/tesla_aladdin_garage/test_deploy_workflow.py:42` — replace `assert "LOCATION_MIN_DELTA_M=80" in text`:

```python
    assert 'LOCATION_MIN_DELTA_M: "40"' in text
    assert "LOCATION_MIN_DELTA_M=${{ env.LOCATION_MIN_DELTA_M }}" in text
    assert "LOCATION_MIN_DELTA_M=80" not in text
```

`test_deploy_workflow.py:46` — `radii["enter_m"] == 500` → `== 300`. Add the regression test that would have
caught the silent no-op (needs `import re` beside the existing `import json`):

```python
def test_signed_config_step_sees_location_delta():
    """gce_signed_telemetry_config reads LOCATION_MIN_DELTA_M from the runner env,
    so a workflow-level env key must exist or the helper silently falls back to 80."""
    text = WF.read_text()
    assert re.search(r'^env:\n(?:  .*\n)*  LOCATION_MIN_DELTA_M: "40"', text, re.MULTILINE)
```

### Verify

```bash
python3 -m pytest -q cloud/tesla_aladdin_garage/test_deploy_workflow.py cloud/tesla_aladdin_garage/test_geofence.py
python3 -c "import yaml,pathlib; d=yaml.safe_load(pathlib.Path('.github/workflows/tesla-aladdin-garage-deploy.yml').read_text()); print(d['env']['LOCATION_MIN_DELTA_M'])"
```

Pass: pytest green; the YAML parse prints `40`, proving the key is at workflow scope and not buried in a step.

## Milestone 3 — Docs lock-step (Composer)

- `cloud/tesla_aladdin_garage/README.md:9` — delta 40 m; radius `300 m / 80 m, exit 380 m`; state that the
  radius is runtime config with `geofence.json` as the seed
- `cloud/tesla_aladdin_garage/README.md:12` — replace "`POST /config` with `enter_m` returns 409" with the
  precedence rule (Firestore wins; file seeds; env ignored) and `enter_m_source` on `/health`
- `cloud/tesla_aladdin_garage/README.md:16` — drop "Worker deletes stale `config.enter_m` overlay on boot"
- `cloud/tesla_aladdin_garage/README.md:51` — `LOCATION_MIN_DELTA_M=80` → `=40`
- `cloud/tesla_aladdin_garage/README.md:59,62` — `/health` now returns `enter_m_source`; `/config` accepts
  `enter_m` / `hysteresis_m` (400 out-of-bounds, 503 unpersisted)
- `RUNBOOK.md:1931` — enter **300 m**, runtime-configurable; note the delta is deploy-time only and takes
  effect only when the signed-config step succeeds
- `RUNBOOK.md:1947` — replace "Radius: edit `geofence.json` and deploy. `POST /config {"enter_m": N}` is 409"
  with the no-deploy recipe:

```bash
# Change the radius with no deploy (takes effect immediately; survives restarts)
curl -sS -X POST "$URL/config" -H "X-Garage-Token: $GARAGE_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"enter_m": 300}'
curl -sS "$URL/health"   # enter_m + enter_m_source=firestore
```

- `PROGRESS.md` — dated 2026-09-13 entry recording the #280 revision and why (lands in this PR; never a
  direct `main` push, per `check_no_main_progress_push.py`)

`notify.py:77` currently tells the operator to edit `geofence.json` and deploy — update it to the `POST /config`
recipe so the email matches the new mechanism.

### Verify

```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 -m pytest -q cloud/tesla_aladdin_garage/test_notify.py
python3 scripts/verify.py --full
```

Pass: doc-freshness clean for the `cloud/**` → RUNBOOK coupling; `verify.py --full` green.

## Per-scenario evidence (PR §4)

| # | Scenario | Pass criterion |
|---|---|---|
| E1 Happy | Radius is runtime-settable | `test_overlay_sets_enter_m` — overlay `{"enter_m": 350}` moves cfg **and** live geofence |
| E2 Happy | Precedence is explicit | `test_boot_prefers_firestore_over_file` — `enter_m_source` is `firestore` with an overlay, `geofence.json` without |
| E3 Happy | Seed + defaults at 300 | `test_load_radii_from_json` asserts `enter_m == 300`, `hysteresis_m == 80` |
| E4 Happy | Delta reaches the path Tesla sees | `test_deploy_workflow.py` pins the workflow `env:` key at `"40"`, the `${{ env.… }}` reference, and the absence of `=80` |
| E5 Failure | Fat-fingered radius rejected | `POST /config {"enter_m": 5}` → 400 `invalid_radii`; live radius unchanged |
| E6 Failure | Unpersisted write surfaces | `save_config` returning `False` → `POST /config` yields 503 `config_not_persisted` (unit) |
| E7 Happy (prod) | **The requirement itself** — no deploy | after merge, `POST /config {"enter_m": 300}` then `GET /health` shows `enter_m=300.0`, `enter_m_source=firestore`, with **no** Cloud Run revision created (`gcloud run revisions list` unchanged) |
| E8 Failure (prod, **blocking**) | Signed telemetry-config landed | deploy log for "Signed fleet_telemetry_config via GCE tesla-http-proxy" shows `http=200` and `updated_vehicles 1`. The step is `continue-on-error: true`, so a green deploy does **not** imply success |
| E9 Legacy (non-blocking) | Real arrival | after the next drive home, Cloud Run logs show consecutive `Location` samples ~40 m apart and a `tesla-aladdin-garage open` event |

Prod commands (E5/E7 mutate config only — never the door):

```bash
URL=$(gcloud run services describe tesla-aladdin-garage --region us-central1 --project jarvis-bhaga-prod --format='value(status.url)')
gcloud run revisions list --service tesla-aladdin-garage --region us-central1 --project jarvis-bhaga-prod --format='value(name)' | head -3
curl -sS -X POST "$URL/config" -H "X-Garage-Token: $GARAGE_ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"enter_m": 300}'
curl -sS "$URL/health"
gh run view <deploy-run-id> --log | grep -A3 'Signed fleet_telemetry_config'
```

## Invariants preserved

- **Exactly one authority** for the radius at any moment, with the winner reported on `/health`. This PR
  moves the authority from git to Firestore; it does not create a second one. `GEOFENCE_ENTER_M` stays ignored.
- First GPS sample never yields `enter` (`geofence.py:51`) — unchanged, so a config change while parked at
  home cannot trigger an open.
- Never `wake_up`; 600 s cooldown; `min=1 max=1` single Cloud Run instance (a second copy double-opens, and
  with in-process overlay state a second copy could also disagree about the radius).
- Admin token still required for `/config` — the radius is now mutable at runtime, so the fail-closed auth
  check at `app.py:61` is load-bearing in a way it was not before.
- Persistence failures are surfaced (503), never swallowed — no silent revert on restart.
- BHAGA untouched: different service, different Firestore database (`garage` vs `(default)`).
- Do **not** run `POST /simulate/enter`: it opens Big Peach for real, and the operator excluded it.
- Tesla cost: at 150,000 streamed signals per dollar, halving the delta is ≈ $0.25/month against
  `TESLA_MONTH_BUDGET_USD=10`. No budget action needed.

## Docs lock-step

`cloud/tesla_aladdin_garage/README.md`, `RUNBOOK.md`, `notify.py` copy, `PROGRESS.md`. Gate:
`python3 scripts/check_doc_freshness.py --base origin/main` (the `cloud/**` → RUNBOOK coupling is already
registered in `scripts/check_doc_freshness.py`).

## Branch / PR mechanics

One branch, one coherent change: `fix/want-to-update-and-reduce-the`.

```bash
bash scripts/install-git-hooks.sh
gh pr create --base main --head fix/want-to-update-and-reduce-the --title "fix(#286): garage radius as runtime config at 300 m, Location delta 40 m" --body "…

Closes #286"
python3 scripts/pr_cost_ledger.py bind-pr --branch fix/want-to-update-and-reduce-the
python3 scripts/pr_cost_ledger.py sync --pr <n>
```

Always `--base main` explicitly. All GitHub ops as `jarvis-agent-bot328`. Never self-merge — babysit to green
via `python3 scripts/pr_triage.py --pr <n>`, reply to every review thread, batch fixes into one push, then
hand back to the operator for the merge gate. Reviewers will ask why #280 is being revised: the PR body must
carry the "keeps the lesson, moves the authority" table above.

## Model routing

M1 Sonnet · M2 Sonnet · M3 Composer, all executed in the operator's current chat per explicit instruction
(*"build this in this same chat"*), so the usual fresh-chat-per-milestone cost discipline is waived here.
`pr_cost_ledger.py sync` before the final push.
