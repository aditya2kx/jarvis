# Issue #310 — Aladdin access token expires after 24 h and is never refreshed

Consulted: [CONTRIBUTING.md](CONTRIBUTING.md) (dev loop + evidence), [.cursor/rules/jarvis.mdc](.cursor/rules/jarvis.mdc) (skills stay generic; never reflexively retry when a side effect can fire), [.cursor/rules/doc-maintenance.mdc](.cursor/rules/doc-maintenance.mdc) (`cloud/**` → RUNBOOK), [docs/plans/i286-geofence-300m-delta40.md](docs/plans/i286-geofence-300m-delta40.md) (house format), [skills/aladdin_connect/client.py](skills/aladdin_connect/client.py) lines 73-113 and 178-192, [skills/tesla_fleet/client.py](skills/tesla_fleet/client.py) lines 134-136 and 226-228 and 305-318, [cloud/tesla_aladdin_garage/worker.py](cloud/tesla_aladdin_garage/worker.py) lines 364-411, [cloud/tesla_aladdin_garage/app.py](cloud/tesla_aladdin_garage/app.py) lines 52-59, [.github/workflows/tesla-aladdin-garage-deploy.yml](.github/workflows/tesla-aladdin-garage-deploy.yml) line 9.

Jam + §4 approved in chat 2026-09-16 and recorded via `phase_state.py advance --operator-approved`.

## Root cause (established during jam, not assumed)

`AladdinConnectClient` logs in once per process and caches the access token forever:

```110:113:skills/aladdin_connect/client.py
    def _token(self) -> str:
        if not self._access_token:
            self.login()
        return self._access_token
```

A live Cognito login during jam returned an access token with `exp - iat == 86400` (exactly 24 h) and that token listed 2 devices, so the credentials are healthy. The Cloud Run instance is always-on (`min=1`, `max=1`, `--no-cpu-throttling`), so one process survives for weeks. Everything past the 24 h mark 401s and stays broken until a redeploy.

Cloud Run log timeline, all on the process that booted 2026-09-13 22:58 UTC:

- 09-14 03:50 — 285.7 m — opened (first login; token good until 09-15 03:50)
- 09-14 23:18 — 266.5 m — opened
- 09-15 03:06 — 266.7 m — opened (44 min before expiry)
- 09-15 23:26 — 267.3 m — `HTTP 401 .../devices` → `open_error` email
- 09-16 01:09 — 267.0 m — `HTTP 401 .../devices` → `open_error` email

`GET /health` corroborates: `opens: 3`, `last_error: "HTTP 401 https://api.smartgarage.systems/devices"`, `needs_reauth: false`.

`TeslaFleetClient` already does this correctly (`_access_exp` at `skills/tesla_fleet/client.py:136`, refresh 60 s early at line 227). This PR closes the asymmetry.

Evidence tier: sandbox-live
scenario: aladdin-live-readonly-devices

Feature flag: **none**. The change is additive failure-recovery on an auth path with no money path, no sheet, and no ledger — it cannot silently produce wrong numbers. No Operator Console UI, so the UX-polish bar is N/A. Out of scope: proactive background refresh, token age on `/health`, alerting on repeated `open_error` — follow-up issues off #310.

Simplest thing first: re-login with the existing `USER_PASSWORD_AUTH` flow rather than adding a `REFRESH_TOKEN_AUTH` path. Credentials are always in env on this surface, so the refresh-token grant buys nothing but a second code path.

## Milestone 1 — Expiry-aware token (Sonnet)

`skills/aladdin_connect/client.py` — add `import time` beside the existing `import os` (line 15 region), and a module constant near `API_BASE` (line 26):

```python
# Cognito issues 24 h access tokens. Re-login before expiry rather than after a 401.
TOKEN_SKEW_S = 300.0
```

`__init__` (line 79) — add `self._access_exp = 0.0` beside `self._access_token`.

`login()` (line 92-108) — record expiry from the Cognito response, which returns `ExpiresIn` in seconds alongside `AccessToken`:

```python
        self._access_exp = time.time() + float(result.get("ExpiresIn") or 3600)
```

`_token()` (line 110-113) — re-login when absent **or** stale:

```python
    def _token(self) -> str:
        if not self._access_token or time.time() >= self._access_exp - TOKEN_SKEW_S:
            self.login()
        return self._access_token
```

### Tests (`skills/aladdin_connect/test_client.py`, appended after line 55)

- `test_token_relogins_when_expired` — `_access_exp` in the past, `_token()` calls `login()`.
- `test_token_reused_while_valid` — `_access_exp` an hour out, `_token()` does not call `login()`.
- `test_login_records_expiry_from_cognito` — patched `_cognito` returning `ExpiresIn: 86400` sets `_access_exp` ~24 h out.
- `test_stale_24h_token_recovers` — the regression pin for this outage: a client whose token was minted 25 h ago returns devices instead of raising `HTTP 401 .../devices`.

### Verify

```bash
python3 -m pytest -q skills/aladdin_connect/test_client.py
```

Pass: all green, including the 25-hour regression pin, which fails on `main`.

## Milestone 2 — One re-login and retry on 401 (Sonnet)

Expiry tracking alone is not enough: Cognito can revoke early, and the deployed process today is already holding a dead token. `_api` (line 178-192) gets exactly one recovery attempt:

```python
    def _api(self, method: str, path: str, payload: Optional[dict] = None,
             *, allow_relogin: bool = True) -> dict:
        ...
        try:
            raw = _http(method, url, headers=headers, payload=body)
        except AladdinError as e:
            # 401 = auth rejected before the device acted, so no door moved: safe to retry.
            # Never retry any other status — a 5xx or timeout may have opened the door.
            if e.status == 401 and allow_relogin:
                log.info("tesla-aladdin-garage aladdin_relogin reason=401 path=%s", path)
                self._access_token = ""
                self._access_exp = 0.0
                return self._api(method, path, payload, allow_relogin=False)
            raise
```

### Tests

- `test_api_relogins_once_on_401` — first `_http` raises `AladdinError(status=401)`, second succeeds; assert `login()` called exactly once and the payload returns.
- `test_api_second_401_propagates` — both calls 401; assert `AladdinError` escapes and `_http` was called exactly twice (no loop).
- `test_api_does_not_retry_non_401` — a 500 propagates with `_http` called once, pinning the no-retry-on-side-effect invariant.

### Verify

```bash
python3 -m pytest -q skills/aladdin_connect/test_client.py cloud/tesla_aladdin_garage/test_worker.py
```

Pass: all green; `test_worker.py` proves `_maybe_open` behaviour (cooldown, already-open skip, `open_error` emit) is unchanged.

## Milestone 3 — Docs lock-step + live evidence (Composer)

- `skills/aladdin_connect/README.md:4` — the AccessToken line gains the 24 h TTL and the auto-refresh/401-retry behaviour.
- `cloud/tesla_aladdin_garage/README.md:20` — the "Aladdin `/devices` uses Cognito AccessToken (IdToken is 401)" bullet gains: token is valid 24 h, the client re-logins before expiry and once on a 401, so a long-lived instance no longer needs a redeploy to recover.
- `RUNBOOK.md:2064` region — add a row to the garage table: `open_error` with `HTTP 401 .../devices` means the Aladdin token went stale; since #310 the client self-heals, so a recurring 401 now indicates a real credential problem, not expiry.
- `PROGRESS.md` — dated 2026-09-16 entry recording the outage, the 24 h TTL finding, and the fix. Lands in this PR; never a direct `main` push (`check_no_main_progress_push.py`).

### Live evidence — real API, read-only, no `OPEN_DOOR` (operator excluded the real door open)

```bash
python3 - <<'PY'
import os, time
from google.cloud import secretmanager
c = secretmanager.SecretManagerServiceClient()
g = lambda n: c.access_secret_version(request={"name": f"projects/jarvis-bhaga-prod/secrets/{n}/versions/latest"}).payload.data.decode()
os.environ["ALADDIN_USERNAME"], os.environ["ALADDIN_PASSWORD"] = g("aladdin-connect-username"), g("aladdin-connect-password")
from skills.aladdin_connect.client import AladdinConnectClient
cl = AladdinConnectClient.from_env(dry_run=True)
cl.login()
cl._access_exp = time.time() - 1          # force the 24h-expired state
print("devices:", len(cl.list_devices()))
print("door:", cl.resolve_door(serial="F0AD4E3E7403", door_index=1))
PY
```

Pass: 2 devices returned and Big Peach resolves, proving `list_devices` → `list_doors` → `resolve_door` — every call the failing crossings made — recovers from an expired token. `OPEN_DOOR` is never sent.

### Verify

```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 scripts/verify.py --full
```

## Per-scenario evidence (PR §4)

- **E1 happy — expiry respected**: `test_token_relogins_when_expired` + `test_token_reused_while_valid`.
- **E2 failure/recovery — 401 recovers once**: `test_api_relogins_once_on_401`; `login()` called exactly once.
- **E3 failure — no retry loop**: `test_api_second_401_propagates`; `_http` called exactly twice.
- **E4 failure — side-effect guard**: `test_api_does_not_retry_non_401`; a 500 is not retried.
- **E5 regression pin — this outage**: `test_stale_24h_token_recovers`; fails on `main`, passes on the branch.
- **E6 live (read-only)**: forced-expiry `list_devices()` returns 2 devices and `resolve_door` resolves Big Peach against the real API.
- **E7 deploy health**: after rollout, `curl -sS "$URL/health"` shows `last_error` empty, `needs_reauth: false`, `enter_m: 300.0`, `enter_m_source: firestore` (radius config untouched).
- **E8 post-merge §4 command, the one that proves the bug is dead**: more than 24 h after deploy, `/health` `last_error` still empty and the next crossing logs `tesla-aladdin-garage open door=Big Peach`. Posted on #310 and checked the following day.

## Invariants preserved

- Retry only on 401 (auth rejected before the device acted). Never on 5xx/timeout — `.cursor/rules/jarvis.mdc`: never reflexively retry when a side effect can fire.
- Exactly one recovery attempt; a second consecutive 401 propagates to `_maybe_open`, which still emits the `open_error` email. A broken credential must stay loud.
- `skills/aladdin_connect/` stays generic — no garage-specific tuning (skills ≠ agents).
- `dry_run` semantics untouched: `open_door` still short-circuits before any HTTP.
- `_id_token` still captured but unused; AccessToken remains the only bearer.
- Single Cloud Run instance, cooldown 600 s, never `wake_up` — all untouched.
- No `POST /simulate/enter` anywhere in the evidence pack; the operator excluded the real door open.
- BHAGA untouched: different service, different Firestore database.

## Branch / PR mechanics

One branch, one coherent change: `fix/my-last-couple-of-attempts-of`.

```bash
bash scripts/install-git-hooks.sh
gh pr create --base main --head fix/my-last-couple-of-attempts-of \
  --title "fix(#310): Aladdin token expires after 24h — re-login before expiry and retry once on 401" \
  --body "...

Refs #310"
python3 scripts/pr_cost_ledger.py bind-pr --branch fix/my-last-couple-of-attempts-of
python3 scripts/pr_cost_ledger.py sync --pr <n>
```

Always `--base main` explicitly. All GitHub ops as `jarvis-agent-bot328`. Never self-merge — babysit to green via `python3 scripts/pr_triage.py --pr <n>`, reply to every review thread, batch fixes into one push, then hand back for the merge gate.

The deploy workflow already triggers on `skills/aladdin_connect/**` (`.github/workflows/tesla-aladdin-garage-deploy.yml:9`), so merging rebuilds and rolls out automatically. That rollout also restarts the process, which clears the currently-stale token — the door is still broken until then.

## Model routing

M1 Sonnet · M2 Sonnet · M3 Composer, per [docs/contributing/cost.md](docs/contributing/cost.md). `pr_cost_ledger.py sync` before the final push.