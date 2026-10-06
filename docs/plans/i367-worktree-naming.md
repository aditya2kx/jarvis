# Worktree naming cap (Issue #367)

Evidence tier: unit-only
waiver: scripts-only worktree naming; no payroll, Cloud Run, or Operator Console runtime path

## Jam / §4 (approved 2026-10-06)

`new_requirement.default_worktree_path` (`scripts/new_requirement.py:251-255`) builds
`../<current-folder>-wt-<slug>` from `repo_root.name` and an uncapped `pr_cost_ledger._slug`.
A task started inside a worktree stacks another `-wt-fix-…`. One sibling folder is 253 bytes
(full path 298). macOS `NAME_MAX` is 255, so the next child hits `ENAMETOOLONG`, Cursor cannot
`mkdir` a terminal, and background shells (5-minute ADP runs) never spawn.

Name every new worktree from the main checkout: `jarvis-wt-<short slug>`. Directory name
≤ 60 bytes. Absolute path ≤ 200 bytes. `scripts/check_worktree_path_length.py` is a hard
`verify.py` gate for `--fast` and `--full`. The gate exercises the helper. It does not scan
worktrees already on disk.

### Per-scenario evidence (PR §4)

| Scenario | Pass |
|---|---|
| Happy path — main checkout | `default_worktree_path(Path("…/jarvis"), "fix/cost-ledger-decontamination")` stays `…/jarvis-wt-fix-cost-ledger-decontamination` (`scripts/test_new_requirement.py:88`) |
| Failure — started from a linked worktree | A temp root whose `.git` file points at `jarvis/.git/worktrees/…`, including a `jarvis-i324`-style name and a stacked `jarvis-wt-…-wt-…` name, produces `jarvis-wt-<slug>` |
| Failure — cap | A branch longer than 60 characters yields a directory name ≤ 60 bytes |
| Failure — path ceiling | The helper and `create_worktree` raise `SystemExit` when the absolute path exceeds 200 bytes, including an explicit `--worktree` |
| Recovery — gate | `python3 scripts/check_worktree_path_length.py` exits 1 if the helper would emit a stacked or over-long path, and exits 0 for this checkout |
| Front door | `python3 scripts/new_requirement.py --requirement "do the thing" --dry-run` exits 0 and prints a `jarvis-wt-` path ≤ 60 bytes (`TestAssertion9`) |
| Lookup | `dev_event_router._derive_worktree_path` and `dev_event_listener._worktree_path_for` resolve that same capped path. Cache-first `worktree_path` is unchanged |

## Invariants

- Idempotent naming: the same main checkout + branch always yields the same sibling path.
- Must not break `--dry-run` (no `input()`, vague text still accepted).
- Must not scan or rename existing long folders.
- Cache-first lookup stays first; derivation is only the fallback.
- No feature flag. Flag decision: this cannot silently produce wrong payroll numbers. Out of scope: deleting old worktrees, ADP runtime, Operator Console UI.

## Milestone 1 — shared namer (Sonnet)

`scripts/new_requirement.py:251` replace `default_worktree_path` and reject inside `create_worktree` (`scripts/new_requirement.py:258`).

```python
MAX_WORKTREE_NAME_BYTES = 60
MAX_WORKTREE_PATH_BYTES = 200

def base_repo_name(repo_root: Path) -> str:
    """Main checkout directory name. Linked worktrees read the .git gitdir file."""

def worktree_dirname(base_name: str, branch: str) -> str:
    """`<base>-wt-<slug>` trimmed so the directory name is ≤ 60 bytes."""

def default_worktree_path(repo_root: Path, branch: str) -> Path:
    """Sibling `../<main>-wt-<slug>`. Raises SystemExit when the absolute path exceeds 200 bytes."""

def _reject_long_worktree_path(path: Path) -> None:
    """SystemExit when len(absolute path) > 200."""
```

`base_repo_name`: if `repo_root / ".git"` is a file containing `gitdir: …/.git/worktrees/<id>`, return the parent of that `.git`. Otherwise return `repo_root.name` (keeps the unit test at `scripts/test_new_requirement.py:88`, which passes a path with no `.git`).

Slug source stays `pr_cost_ledger._slug`. Trim the slug so `f"{base}-wt-{slug}"` encodes to ≤ 60 bytes.

`create_worktree` calls `_reject_long_worktree_path` before the exists-check, so an explicit `--worktree` over 200 bytes is rejected on dry-run too. Help text at `scripts/new_requirement.py:591-593` says the default is `../<main-checkout>-wt-<slug>` capped at 60 bytes.

`scripts/new_worktree.py:54` calls `default_worktree_path` instead of hardcoding `jarvis-wt-`. An explicit `--worktree-dir` still goes through `_reject_long_worktree_path`.

**Verify:**

```bash
python3 -m pytest scripts/test_new_requirement.py -q --tb=short
```

Pass: existing `test_default_worktree_path` green; new tests cover linked-worktree, 60-byte cap, and `SystemExit` over 200 bytes.

## Milestone 2 — lookup + gate (Sonnet)

`scripts/dev_event_router.py:106-117` `_derive_worktree_path` and `scripts/dev_event_listener.py:549-563` `_worktree_path_for` (after the cache hit) call `default_worktree_path(REPO_ROOT, branch)` and return it only when `is_dir()`. `SystemExit` from an over-long path becomes `None`. Phase-cache filename slugs stay as they are.

`scripts/check_worktree_path_length.py`:

```bash
python3 scripts/check_worktree_path_length.py
```

Checks, in process, no `git worktree list` scan:

1. Current checkout + a long branch → name ≤ 60 bytes, path ≤ 200 bytes, prefix `<base>-wt-`.
2. Temp linked worktree whose folder name is already stacked → result is `jarvis-wt-<slug>`, not `<stacked>-wt-…`.
3. Exit 1 with a greppable `check_worktree_path_length:` line on any miss.

Wire into `scripts/verify.py` `GATES` (after `progress-push-guard`, ~line 147) as hard, modes `fast` and `full`:

```python
Gate(
    name="worktree-path-length",
    argv=["python3", "scripts/check_worktree_path_length.py"],
    hard=True,
    modes={"fast", "full"},
)
```

**Verify:**

```bash
python3 scripts/check_worktree_path_length.py
python3 -m pytest scripts/test_check_worktree_path_length.py scripts/test_dev_event_router.py scripts/test_dev_event_listener.py scripts/test_verify.py -q --tb=short
```

Pass: check exits 0; router fallback test still finds `jarvis-wt-<slug>`; a new test finds that short path when `REPO_ROOT` is a stacked linked worktree.

## Milestone 3 — docs + front door (Sonnet)

`docs/WORKFLOW.md:132` (after the branch-naming bullets): one paragraph stating the directory rule, the 60-byte / 200-byte caps, and that the gate does not scan existing folders. Add `scripts/check_worktree_path_length.py` to the New scripts table (~line 422).

No `RUNBOOK.md`. No `PROGRESS.md` in this PR (retrospective writes that later).

**Verify:**

```bash
python3 scripts/new_requirement.py --requirement "do the thing" --dry-run
python3 scripts/check_doc_freshness.py
python3 scripts/verify.py --full --plan docs/plans/i367-worktree-naming.md
```

Pass: dry-run exits 0 and the printed worktree path is `jarvis-wt-…` ≤ 60 bytes; doc-freshness clean; `verify.py --full` green.

## PR mechanics

One branch `fix/fix-worktree-naming-new-requirement-py`. `gh pr create --base main` as `jarvis-agent-bot328`, `Closes #367`. Never self-merge. Babysit via `pr_triage.py`. Reply on every review thread. `--no-verify` push only after `verify.py --full` is green.

Model routing: Sonnet for all three milestones (scripts + docs). Opus already used for the jam.
