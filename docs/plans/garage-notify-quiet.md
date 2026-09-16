# Issue #316 — Garage Gmail quiet — only mail a real live open

Consulted: [CONTRIBUTING.md](CONTRIBUTING.md) (dev loop + evidence), [RUNBOOK.md](RUNBOOK.md) lines 2065–2076, [cloud/tesla_aladdin_garage/README.md](cloud/tesla_aladdin_garage/README.md) lines 21 and 61, [cloud/tesla_aladdin_garage/notify.py](cloud/tesla_aladdin_garage/notify.py) lines 32–36 and 84–103, [cloud/tesla_aladdin_garage/worker.py](cloud/tesla_aladdin_garage/worker.py) lines 143–159, 319–411, [docs/plans/i310-aladdin-token-refresh.md](docs/plans/i310-aladdin-token-refresh.md) (open_error used to stay loud in Gmail; operator now says the inbox is unusable), `.cursor/rules/jarvis.mdc` (never reflexively retry a side-effecting open; leave a greppable breadcrumb).

Jam + §4: operator in this chat 2026-09-16 — Tesla already home, 5–8 emails; “investigate and fix in the same workspace”; “sick of getting new emails.”

## Root cause

Inbox dump at 15:18 UTC 2026-09-16 was **not** a drive:

- Burst of `simulated` 530 m (`enter_m + hysteresis + 50`) plus 0 m home-pin mails.
- Mix of enter 400 m and 300 m → more than one worker/state.
- `send_garage_email` fires on `opened`, `skip_already_open`, and `open_error` including simulated.
- `last_open_ts` is **not** in Firestore `_snapshot` (`worker.py:143-156`), so every Cloud Run restart / second process has no cooldown.
- `open_error` does not set `last_open_ts` (`worker.py:406-411`) → 401 retries re-mailed (Sep 15–16).
- `POST /simulate/enter` always opens even when last live Tesla metres are already inside (`worker.py:319-347`).

Evidence tier: unit-only
waiver: garage has no BHAGA sandbox; live `POST /simulate/enter` opens Big Peach and is the noise source — excluded. Post-merge: read-only `/health` + Cloud Logging `skip reason=notify_quiet` / `simulate_already_inside`. Never send a live simulate.

Feature flag: **none**. Notify policy cannot silently produce wrong BHAGA numbers. No Operator Console UI.

## Milestone 1 — Quiet Gmail (Composer)

`cloud/tesla_aladdin_garage/notify.py` after `_SUBJECTS` (line 36):

```python
def should_email(event: str, fields: dict[str, Any]) -> bool:
    """Gmail only for a real (non-simulated) door command."""
    if fields.get("simulated"):
        return False
    return event == "opened"
```

`send_garage_email` (line 84): if not `should_email`, log `skip reason=notify_quiet event=` and return False **before** Gmail OAuth.

### Tests (`test_notify.py`)

- `test_should_email_only_real_opened`
- `test_send_skips_simulated_even_when_gmail_configured` (Gmail helpers must not be called)

### Verify

```bash
python3 -m pytest -q cloud/tesla_aladdin_garage/test_notify.py
```

## Milestone 2 — Persist cooldown + skip home simulate (Composer)

`WorkerState` already has `last_open_ts`. `_snapshot` (`worker.py:143`) adds `"last_open_ts": self.state.last_open_ts`.

`GarageWorker.__init__` after overlay (`worker.py:125-129`) calls `_restore_state(persist.load_state())`:

```python
def _restore_state(self, stored: dict) -> None:
    if not stored:
        return
    dist = stored.get("last_distance_m")
    if dist is not None:
        self.state.last_distance_m = float(dist)
        self.geofence.inside = self.state.last_distance_m <= self.cfg.enter_m
        self.state.last_event = "inside" if self.geofence.inside else "outside"
    ts = stored.get("last_open_ts")
    if ts is not None:
        self.state.last_open_ts = float(ts)
```

`simulate_enter` (`worker.py:319`): if `last_distance_m is not None and last_distance_m <= enter_m`, log `skip reason=simulate_already_inside` and return `skip_already_inside` **without** opening.

`_emit` (`worker.py:349`): still calls `_notify`; quiet is in `should_email`. Worker tests that inject `notify=` still see events; they must assert **email policy** via `should_email` or by wrapping.

`_maybe_open` open_error branch: set `self.state.last_open_ts = now` so a failing Aladdin does not re-command every GPS re-enter.

Already-open: keep skip command; `_emit` still invoked for logs/tests; Gmail quiet.

### Tests (`test_worker.py`)

- `test_already_open_skips_command_and_notifies` stays (callback still fired); add `assert should_email("skip_already_open", …) is False`
- `test_simulate_skipped_when_tesla_already_inside`
- `test_simulate_opens_when_no_live_fix`
- `test_restore_last_open_ts_cooldown`
- `test_open_error_sets_cooldown`

### Verify

```bash
python3 -m pytest -q cloud/tesla_aladdin_garage/test_worker.py cloud/tesla_aladdin_garage/test_notify.py
```

## Milestone 3 — Docs lock-step (Composer)

- `README.md:21` — Gmail only on real `opened`; simulate / already-open / open_error are log-only (`skip reason=notify_quiet`). Simulate while last Tesla metres ≤ enter_m → `skip_already_inside`.
- `README.md` skip-reason list (line 69) add `notify_quiet`, `simulate_already_inside`.
- `RUNBOOK.md:2065` — same notify policy; simulate curl comment: no-ops with email when car is already inside.

### Verify

```bash
python3 scripts/check_doc_freshness.py --base origin/main
python3 -m pytest -q cloud/tesla_aladdin_garage/test_notify.py cloud/tesla_aladdin_garage/test_worker.py
```

## Per-scenario evidence (PR §4)

- **E1 happy**: real `opened` still `should_email` True; `test_open_notifies`.
- **E2 simulate noise**: simulated `opened` → `should_email` False; send never hits Gmail.
- **E3 already home simulate**: `last_distance_m=18` → `skip_already_inside`, no `open_door`.
- **E4 already open**: no `OPEN_DOOR`; `should_email` False.
- **E5 open_error cooldown**: second enter inside 600 s is `skip_cooldown`.
- **E6 restart cooldown**: restored `last_open_ts` → `skip_cooldown`.
- **E7 post-merge**: `GET /health` (no simulate). Logs after any admin simulate while 18 m inside: `simulate_already_inside`.

## Invariants

- Integer metres in subjects unchanged for the one remaining email.
- Cooldown 600 s, max instances 1, never `wake_up`.
- No `POST /simulate/enter` in evidence.
- BHAGA untouched.

## Branch / PR

Same worktree (operator). `gh pr create --base main`. Bot identity. Never self-merge.

## Model routing

Composer throughout; garage units only.
