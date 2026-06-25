# Plan: Add docs/dogfood/README.md
<!-- authored by claude-opus-4-8-thinking-high subagent [c1d80565-987b-46bf-a36d-fe9f211ac817] -->

> **Phase:** 5 (plan) · **Driver:** agent · **Tracking issue:** [#66](https://github.com/aditya2kx/jarvis/issues/66)
> **Scope:** doc-only. One new file + one `PROGRESS.md` dated entry. No code, no tests, no harness-stub edits, no committed transcripts.
> **Source of truth consulted:** `AGENTS.md`, `CONTRIBUTING.md`, `.cursor/rules/plan-execution-readiness.md`, `docs/WORKFLOW.md §1/§7`, `scripts/dogfood_lifecycle.py` (`DOGFOOD_DOC` 573–588, `main()` 630–648, module docstring 2–22, `DEFAULT_STATE` 45), `docs/dogfood/evidence/01-jam-ask-mode.md`.

---

## Objective

Add a permanent, production-quality `docs/dogfood/README.md` that explains what the `docs/dogfood/` directory is, how `scripts/dogfood_lifecycle.py` produces its transcripts, the four step-marker annotation types, the four subcommands, the state-file location, and a cross-reference to `docs/WORKFLOW.md §7`. This is **distinct** from the throwaway 16-line `DOGFOOD_DOC` stub (`scripts/dogfood_lifecycle.py:573–588`), which is intentionally left untouched.

---

## Checklist-item coverage (plan-execution-readiness.md, 10/10)

| # | Item | Where addressed |
|---|---|---|
| 1 | Exact file path + line number per change | "Changes" table below + each milestone |
| 2 | Concrete artifacts inline (stub, CLI, env) | "Concrete artifact: README content" + Verify blocks |
| 3 | ≥3 milestones, each with a verifiable test command | M1, M2, M3 (each has a `Verify:` bash block) |
| 4 | Per-scenario evidence (happy + failure/recovery) | "Evidence (PR §4)" + "Failure/recovery scenarios" |
| 5 | Sandbox tier stated | "Sandbox tier" section (Tier-1 e2e; no Tier-2) |
| 6 | Invariants explicitly preserved | "Invariants preserved" section |
| 7 | Feature-flag decision made | "Feature-flag decision" section (no flag) |
| 8 | Docs lock-step targets listed | "Docs lock-step" section (`PROGRESS.md`) |
| 9 | Branch/PR mechanics noted | "Branch / PR mechanics" section |
| 10 | Model routing per milestone | "Model routing" column in each milestone + table |

---

## Changes (exact file:line)

| File | Line | Action | Notes |
|---|---|---|---|
| `docs/dogfood/README.md` | `:1` (new file) | **Create** | ~50–80 lines; full content drafted below |
| `PROGRESS.md` | append a new `## <date> — Dogfood:` section at top of the dated-entries list | **Edit (append)** | one dated entry; must contain the literal token `Dogfood` for evidence block 4 |

**Explicitly NOT changed** (guard against scope creep):
- `scripts/dogfood_lifecycle.py:573–588` (`DOGFOOD_DOC` stub) — left as-is.
- Any `*.py` test file.
- Any `docs/dogfood/lifecycle-run-*.md` transcript — none committed in this PR.

---

## Concrete artifact: `docs/dogfood/README.md` (full draft, copy-pasteable)

```markdown
# Dogfood evidence

This directory holds **end-to-end lifecycle conformance runs** produced by
`scripts/dogfood_lifecycle.py`. Each `lifecycle-run-<date>.md` is an annotated
transcript proving that a dummy requirement walked all 12 substeps of the Jarvis
lifecycle (`scripts/lifecycle.py`) against **real** infrastructure — a real
tracking issue, a throwaway PR off `origin/main`, real operator-gate enforcement,
and a real operator merge.

> This README is permanent documentation. It is **not** the same as the throwaway
> `DOGFOOD_DOC` stub inside `scripts/dogfood_lifecycle.py` (lines 573–588), which is
> the doc the harness writes during a mechanical dummy run.

## What the orchestrator does

`dogfood_lifecycle.py` drives a trivial requirement ("Add docs/dogfood/README.md")
through every lifecycle substep, pausing at the operator-reserved `merge` gate
because the bot is a non-admin and branch protection forces a human approval.

## Step-marker annotation types

Each substep in a transcript is tagged with one of four markers:

| Marker | Meaning |
|---|---|
| `SEEDED` | Substep was factually complete the moment `new_requirement` ran. |
| `HARNESS-DRIVEN` | The agent/harness drove this substep autonomously. |
| `OPERATOR-SIMULATED` | Harness simulated operator approval to demo a gate. |
| `OPERATOR-REAL` | A real operator merge (the one requiring your GitHub click). |

## Subcommands

| Command | What it does |
|---|---|
| `run` | Walks the 8 agent substeps + 2 gate demos, opens the dummy PR, pauses at `merge`. |
| `resume` | After the operator merges the dummy PR: merge + post-merge-verify + retrospective; writes the transcript. |
| `check` | Offline, deterministic conformance assertions on the recorded run (safe in CI). |
| `cleanup` | Removes the state file / tears down the dummy run. |

## State file

Run state is persisted at `metrics/pr_cost/dogfood-state.json` (gitignored). It is
read by `resume`, `check`, and `cleanup`.

## Reproduce a run

```bash
python3 scripts/dogfood_lifecycle.py run        # opens dummy PR, pauses at merge gate
# operator approves + squash-merges the dummy PR, then:
python3 scripts/dogfood_lifecycle.py resume      # finishes lifecycle, writes transcript
python3 scripts/dogfood_lifecycle.py check       # offline conformance assertions
python3 scripts/dogfood_lifecycle.py cleanup     # remove state when done
```

## See also

- `docs/WORKFLOW.md §7` — Verification matrix; the dogfood guarantee row lives here.
- `scripts/dogfood_lifecycle.py` — the orchestrator (module docstring documents the flow).
```

---

## Concrete artifact: `PROGRESS.md` dated entry (copy-pasteable)

```markdown
## 2026-06-23 — Dogfood: documented the evidence directory (issue #66)

Added `docs/dogfood/README.md` documenting the dogfood evidence directory: its
purpose, the four step-marker types (SEEDED / HARNESS-DRIVEN / OPERATOR-SIMULATED /
OPERATOR-REAL), the four `dogfood_lifecycle.py` subcommands (run/resume/check/cleanup),
the gitignored state file at `metrics/pr_cost/dogfood-state.json`, and a cross-reference
to `docs/WORKFLOW.md §7`. Doc-only, additive, no code/test changes; the
`DOGFOOD_DOC` harness stub (`scripts/dogfood_lifecycle.py:573–588`) was intentionally
left untouched.
```

---

## Milestones

### Milestone 1 — Create the README (Model: **Sonnet**)
**Change:** create `docs/dogfood/README.md:1` with the full draft above.
**Pass criterion:** file exists, first line is exactly `# Dogfood evidence`, secret-scan + changed-doc freshness clean.

**Verify:**
```bash
test -f docs/dogfood/README.md \
  && head -1 docs/dogfood/README.md | grep -qx '# Dogfood evidence' \
  && python3 scripts/verify.py --fast \
  && echo "M1 OK"
```

### Milestone 2 — Lock-step PROGRESS.md + doc-freshness (Model: **Sonnet**)
**Change:** append the dated `Dogfood` entry to `PROGRESS.md` (top of dated list).
**Pass criterion:** `check_doc_freshness.py` exits 0 and `grep "Dogfood" PROGRESS.md` returns a dated line.

**Verify:**
```bash
python3 scripts/check_doc_freshness.py \
  && grep -q "Dogfood" PROGRESS.md \
  && echo "M2 OK"
```

### Milestone 3 — Verify-full, branch, PR, babysit (Model: **Sonnet**; **Opus only if stuck**)
**Change:** no file edits beyond M1/M2; run the full gate, create the branch + PR, babysit CI to green. Never self-merge.
**Pass criterion:** `verify.py --full` exits 0 and the diff against `origin/main` is exactly the two expected files.

**Verify:**
```bash
python3 scripts/verify.py --full \
  && git diff --name-only origin/main | sort | tr '\n' ' ' \
       | grep -qx 'PROGRESS.md docs/dogfood/README.md ' \
  && echo "M3 OK"
```

---

## Evidence (PR §4) — per-scenario, enumerated

Mirrors the jam contract in `docs/dogfood/evidence/01-jam-ask-mode.md §4`.

**Happy path:**
1. `ls docs/dogfood/` shows `README.md`; `head -5 docs/dogfood/README.md` first heading is `# Dogfood evidence`.
2. `python3 scripts/verify.py --fast` → exit 0 (`Verify PASSED.`).
3. `python3 scripts/check_doc_freshness.py` → exit 0 (`all coupled docs updated. ✓`).
4. `grep "Dogfood" PROGRESS.md | tail -1` → a dated `## <date> — Dogfood:` line.
5. `git diff --name-only origin/main` → exactly `docs/dogfood/README.md` and `PROGRESS.md`, no `.py` changes.

**Failure / recovery scenarios:**
- **doc-freshness fails** (coupling rule trips unexpectedly): read the reported coupled-doc requirement, update the doc it names, re-run `check_doc_freshness.py` until exit 0. Do not suppress the gate.
- **`git diff` shows extra files** (e.g. a stray `dogfood-state.json` or transcript): confirm `metrics/pr_cost/dogfood-state.json` is gitignored; `git restore`/remove any unintended file so the diff is exactly two files. Never commit `lifecycle-run-*.md`.
- **`verify.py --full` red on a pre-existing unrelated failure**: do not fix out of scope; capture the failure, confirm it is unrelated to the two changed files, and escalate to the operator rather than expanding scope.

---

## Sandbox tier

**Tier-1 e2e only.** This is a doc-only change with no live-only code path touched. Verification is the local `verify.py --fast`/`--full` mirror of CI.

---

## Invariants preserved

- **Additive:** only a new file + an appended `PROGRESS.md` entry; nothing removed or rewritten.
- **Backward-compatible:** no code, schema, CLI, or runtime-behavior changes; existing `dogfood_lifecycle.py` flow (incl. `DOGFOOD_DOC` 573–588) is byte-identical.
- **Idempotent:** re-running yields the same file; no state mutation.
- **No code changes:** zero `.py`/test edits — enforced by evidence block 5 (`git diff --name-only origin/main`).

---

## Feature-flag decision

**No flag needed.** Documentation file has no runtime code path and cannot influence any computed value.

---

## Docs lock-step

- **Primary lock-step target:** `PROGRESS.md` (dated entry — Milestone 2).
- **Gate:** `python3 scripts/check_doc_freshness.py` must exit 0.
- **Cross-reference only (no edit required):** `docs/WORKFLOW.md §7` is *referenced from* the new README; it is not modified.

---

## Branch / PR mechanics

- **Branch:** `fix/add-docs-dogfood-readme-md-documenting`, cut from `origin/main`.
- **Open PR:** `gh pr create --base main --title "docs: add dogfood evidence directory README" --label "no-e2e"`.
- **Never self-merge.** Phase 9 is operator-reserved; operator squash-merges.
- **Babysit immediately** after the PR is up per `pr-workflow.mdc` + babysit skill.
- **One chat per PR.**

---

## Model routing (per milestone)

| Milestone | Model | Rationale |
|---|---|---|
| M1 — write README | **Sonnet** | Straightforward doc authoring. |
| M2 — PROGRESS.md + freshness | **Sonnet** | Mechanical append + gate. |
| M3 — verify/branch/PR/babysit | **Sonnet**, escalate to **Opus only if stuck** | Routine ship. |
