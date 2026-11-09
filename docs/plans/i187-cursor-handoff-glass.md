# Issue #187 — Cursor handoff opens Agents Window via `--glass`

Evidence tier: unit-only
waiver: full Cursor window open is a Mac GUI side effect; CI proves argv + fallback. Live CLI advertises `--glass` (verified on operator Mac).

## Jam / §4 (approved in chat 2026-11-08)

Operator: after #509 deploy, investigate and fix `/jarvis-new-task` Cursor-open failure. Root cause: `open_cursor_handoff` used `cursor -n` (classic IDE) + `/glass` deeplink; Cursor now has native `--glass`. Issue #187 already tracks this.

1. `open_cursor_handoff` → `cursor --glass -n <folder>` then prompt deeplink (no `/glass` deeplink when `--glass` supported).
2. `attach_worktree_here` → `cursor --glass -r <folder>`.
3. `handoff.py in` uses the same argv helper.
4. Legacy CLI without `--glass`: fall back to `cursor -n` + `/glass` deeplink.
5. CLI open failure → greppable `jarvis-handoff:` breadcrumb + open HTML launcher.
6. Docs: `WORKFLOW.md`, `dev-anywhere.md`.

Feature flag: **not flagged** — cannot silently produce wrong tip/payroll numbers; handoff-only UX path.

## Per-scenario evidence

1. Unit: `cursor_open_argv(..., glass=True)` → `[cursor, --glass, -n, folder]`.
2. Unit: reuse attach → `--glass -r`.
3. Unit: `glass=False` → no `--glass`.
4. Unit: `cursor_supports_glass` parses `--help`.
5. Unit: `open_cursor_handoff` Popen argv includes `--glass`; no glass deeplink `open`.
6. Unit: Popen `OSError` → `open launch_html` + breadcrumb on stderr.
7. `python3 scripts/verify.py --full` green.
8. Live (pre-PR): `/Applications/Cursor.app/.../cursor --help` lists `--glass`.

## Citations

- `scripts/start_pr_session.py:439` `prefer_attach_here`
- `scripts/start_pr_session.py:484` `find_cursor_cli`
- `scripts/start_pr_session.py:501` `open_cursor_handoff` (replaced)
- `scripts/start_pr_session.py:456` `attach_worktree_here` (replaced)
- `scripts/handoff.py:175` Mac open path in `cmd_in`
- `scripts/new_requirement.py:624` `_run_one` attach vs open branch
- `docs/WORKFLOW.md:110` Agents Window attach paragraph
- `docs/contributing/dev-anywhere.md:11` Start work row
- CONTRIBUTING.md §4 evidence contract

## Stubs

```python
def cursor_supports_glass(cursor: Path | None = None) -> bool: ...
def cursor_open_argv(
    cursor: Path, folder: Path, *, reuse: bool = False, glass: bool | None = None,
) -> list[str]: ...
# open_cursor_handoff → Popen(cursor_open_argv(...)); legacy: open(make_glass_deeplink())
# attach_worktree_here → Popen(cursor_open_argv(..., reuse=True))
```

## Docs lock-step

- `docs/WORKFLOW.md` — Agents Window + `--glass` open
- `docs/contributing/dev-anywhere.md` — Start work row
- `docs/plans/i187-cursor-handoff-glass.md` — this plan
- `check_doc_freshness.py` after edit

## Branch / PR mechanics

- Branch: `fix/i187-cursor-handoff-glass` (Closes #187)
- Push as `jarvis-agent-bot328`; never self-merge; `--base main`
- Reply to every review thread; babysit via `pr_triage.py`

## Model routing

| Milestone | Model |
|---|---|
| M1 argv helpers + open/attach | Composer |
| M2 tests + docs | Composer |
| M3 verify + PR babysit | Composer |

## Milestones

### M1 — argv helpers + handoff (Composer)

Edit `scripts/start_pr_session.py` + `scripts/handoff.py` as above.

**Verify:** `python3 -c "from scripts import start_pr_session as S; print(S.cursor_open_argv.__name__)"`
**Pass:** helpers import; no classic-only `-n` without `--glass` when supported.

### M2 — unit tests + docs (Composer)

Add tests in `scripts/test_start_pr_session.py`; update WORKFLOW + dev-anywhere.

**Verify:** `python3 -m unittest scripts.test_start_pr_session -v`
**Pass:** new glass/fallback cases green.

### M3 — verify + ship (Composer)

**Verify:** `python3 scripts/verify.py --full`
**Pass:** all gates green; PR opened with §4 evidence; babysit to green; operator merges.

## Invariants

- Glass attach (`prefer_attach_here`) unchanged: still prints `CONTINUE_IN_THIS_CHAT=1`.
- No secrets in git; no prod mutation; integer-cents / ADP invariants N/A.
- Surgical: handoff path only.
