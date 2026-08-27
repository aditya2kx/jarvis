# Issue #280 — Single SoT for garage enter radius (500 m)

Consulted: `CONTRIBUTING.md` (dev loop + evidence), `docs/contributing/sandbox-evidence.md`, `docs/contributing/prod-changes.md` (flag test: not BHAGA money), `RUNBOOK.md` Tesla Aladdin garage (~line 1931), `cloud/tesla_aladdin_garage/worker.py` `WorkerConfig.from_env` lines 40–71 + overlay lines 109–111, `persist.py` `load_config`/`save_config` lines 67–86, `.github/workflows/tesla-aladdin-garage-deploy.yml` line 77 `GEOFENCE_ENTER_M=800`.

Jam / §4 approved in chat 2026-08-27: one git file is SoT; enter **500 m**; hysteresis **80 m**; ignore Firestore overlay for enter; no Tesla portal MFA this PR.

Evidence tier: unit-only
waiver: garage units + prod `/health` and Firestore; no BHAGA sandbox; never `simulate/enter`.

Feature flag: **none**. Radius is the product change (door may open farther than last night’s 200 m overlay). Cannot silently produce wrong BHAGA numbers. No Operator Console UI (polish N/A).

## SoT

New file `cloud/tesla_aladdin_garage/geofence.json`:

```json
{
  "enter_m": 500,
  "hysteresis_m": 80
}
```

Worker reads **only** this file for those two fields. Dockerfile already `COPY cloud/tesla_aladdin_garage` (Dockerfile:12) so the JSON is in the image.

```python
def load_radii(path: Path | None = None) -> tuple[float, float]:
    """Return (enter_m, hysteresis_m) from geofence.json. Raises if missing/invalid."""
```

## Milestone 1 — Load file; ignore overlay and env (Sonnet)

### Files
- `cloud/tesla_aladdin_garage/geofence.json` (new)
- `cloud/tesla_aladdin_garage/geofence.py` — add `load_radii()` (~line 19, before `Geofence`)
- `cloud/tesla_aladdin_garage/worker.py` `WorkerConfig.from_env` lines 48–49: use `load_radii()`, do **not** read `GEOFENCE_ENTER_M` / `GEOFENCE_HYSTERESIS_M`
- `WorkerConfig.apply_overlay` lines 63–67: **skip** `enter_m` and `hysteresis_m`
- `GarageWorker.__init__` lines 109–111: still may load overlay for cooldown/poll; call `persist.clear_geofence_overlay()` so stale `named-db-seed` 200 is deleted
- `cloud/tesla_aladdin_garage/persist.py` — `def clear_geofence_overlay() -> None:` `DELETE_FIELD` on `enter_m`/`hysteresis_m` in `config` doc (merge)
- `cloud/tesla_aladdin_garage/app.py` `config()` lines 113–131: if body contains `enter_m` or `hysteresis_m` → **409** `{"ok": false, "error": "geofence_file_sot"}`

Dataclass defaults `enter_m=800` on `WorkerConfig`/`Geofence` (`worker.py:26`, `geofence.py:23`) become unused for prod; tests that pass `enter_m=` keep working. Change unused defaults to 500 so a missed wire does not revive 800.

Replace `test_overlay_changes_enter_m` (`test_worker.py:171–176`) with: overlay `enter_m` does **not** change radius; `from_env` ignores `GEOFENCE_ENTER_M=200` if set.

### Verify
```bash
python3 -m pytest -q cloud/tesla_aladdin_garage/test_geofence.py cloud/tesla_aladdin_garage/test_worker.py cloud/tesla_aladdin_garage/test_persist.py cloud/tesla_aladdin_garage/test_app.py
```
Pass: all green; overlay cannot set 350.

## Milestone 2 — Deploy YAML has no second radius (Sonnet)

`.github/workflows/tesla-aladdin-garage-deploy.yml` line 77: **remove** `GEOFENCE_ENTER_M=800` and `GEOFENCE_HYSTERESIS_M=80` from `--set-env-vars`.

`cloud/tesla_aladdin_garage/test_deploy_workflow.py` `test_deploy_enables_telemetry_not_rest_poll` lines 42–43: assert those env keys are **absent**; add assert `geofence.json` `enter_m == 500`.

### Verify
```bash
python3 -m pytest -q cloud/tesla_aladdin_garage/test_deploy_workflow.py
```

## Milestone 3 — Docs + notify copy (Composer)

- `RUNBOOK.md` ~1931, ~1947–1949: enter 500 m from `geofence.json`; delete “POST /config enter_m 800”
- `cloud/tesla_aladdin_garage/README.md` lines 9–16, 46, 62: file SoT; overlay does not win
- `notify.py` `email_body` line 77: tell operator to change `geofence.json`, not POST enter_m
- `check_doc_freshness.py` coupling already includes `cloud/tesla_aladdin_garage/**` → README

### Verify
```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 -m pytest -q cloud/tesla_aladdin_garage/test_notify.py
```

## Per-scenario evidence (PR §4)

| # | Scenario | Pass |
|---|---|---|
| E1 Happy | `geofence.json` only writer | grep/tests: no `GEOFENCE_ENTER_M` in deploy YAML |
| E2 Happy | units | pytest garage as M1+M2 |
| E3 Happy | prod after merge/deploy | `curl /health` `enter_m` **500** |
| E4 Recovery | stale overlay | `clear_geofence_overlay`; `/health` still 500 if Firestore still had 200 |
| E5 Failure | `POST /config {"enter_m": 200}` | 409; radius unchanged |
| E6 Legacy | first sample never enter; no wake_up; max-instances 1 | existing tests; BHAGA untouched |

**Post-merge verification** (also captured pre-merge via `workflow_dispatch` run 33094310107):

```
$ curl -sS "$URL/health"  # enter_m
500.0

$ python3 -c "from google.cloud import firestore; d=firestore.Client(project='jarvis-bhaga-prod', database='garage').collection('tesla_aladdin_garage').document('config').get().to_dict() or {}; print(sorted(d))"
['source', 'updated_ts']
```

Live `/health` `enter_m` is **500**. Firestore config overlay no longer contains `enter_m` (200 deleted).

```bash
curl -sS "$(gcloud run services describe tesla-aladdin-garage --region us-central1 --project jarvis-bhaga-prod --format='value(status.url)')/health"
python3 -c "from google.cloud import firestore; d=firestore.Client(project='jarvis-bhaga-prod', database='garage').collection('tesla_aladdin_garage').document('config').get().to_dict() or {}; print(d)"
```

## Invariants

- First GPS sample never `enter` (`geofence.py` `observe` ~line 36)
- Never `wake_up`; cooldown; single Cloud Run instance
- Fail-open notify; fail-closed admin token
- Idempotent overlay delete (DELETE_FIELD if already gone)
- Do not `simulate/enter` (opens Big Peach)

## Docs lock-step

RUNBOOK.md, `cloud/tesla_aladdin_garage/README.md`, notify footer. PROGRESS via post-merge retro (no direct main write). `python3 scripts/check_doc_freshness.py --base origin/main`

## Branch / PR

Branch `fix/sharing-the-screenshot-of-the-last`. `gh pr create --base main --head fix/sharing-the-screenshot-of-the-last` **Closes #280**. Bot `jarvis-agent-bot328`. Never self-merge; babysit; one PR. `python3 scripts/pr_cost_ledger.py bind-pr` after create.

## Model routing

M1–M2 Sonnet. M3 Composer. One chat per PR.
