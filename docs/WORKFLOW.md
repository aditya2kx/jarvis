# Jarvis Workflow — end-to-end lifecycle map

This is the canonical reference for *how work flows through the Jarvis system*:
from "operator has a feature idea" to merged + deployed + retrospective.
AGENTS.md links here as the single "how we work together" entry.

---

## 1. The 10-phase end-to-end lifecycle

Each phase has a driver, a governing artifact, and an exit gate.  The 5 tracking
stages group these phases for the GitHub Issue tracker.

```
Stage      Phase  Driver        Governing artifact          Exit gate
───────────────────────────────────────────────────────────────────────────
ALIGN      1  specify       operator  vision                 requirement statement exists
           2  setup         agent     new_requirement.py     worktree + branch + brief + issue
           3  jam           operator  Ask mode + WORKFLOW    requirements restated + agreed
           4  define-evid.  operator  CONTRIBUTING §evidence operator-approved §4 contract

PLAN       5  plan          agent     plan-execution-        check_plan_readiness.py PASSES
                                      readiness.md

BUILD      6  implement     agent     behavioral anchor +    code written; tests pass
                                      tests
           7  verify        agent     verify.py --full       verify.py --full green

SHIP       8  pr-evidence   agent     pr-workflow.mdc        PR §4 assembled; desc gate passes
           8  babysit       agent     babysit skill          CI green; all comments replied
           9  merge         operator  branch protection      operator squash-merges

VERIFY &  10  post-merge    agent     RUNBOOK.md             prod state matches expected
LEARN      10  retrospective agent→op  doc-maintenance.md    PROGRESS.md + requirements
                                       + skill evolution      updated; back to ideation
```

### Operator-reserved gates
Phases 1, 3, 4, and 9 require the operator.  The operator gives approval **in the
Cursor chat** — there is no need to add a label or type anything on the GitHub issue.

The **phase-consistency gate** (`phase_state.py gate`, registered as a hard gate in
`verify.py --full` and the pre-push hook) makes every substep in the ladder
mechanically unskippable — operator gates and agent substeps alike.  It uses a
declarative `OBSERVABLE_FLOOR` detector registry to compare real-world evidence
against the phase cache:

| Detector fires when… | Evidences substep |
|---|---|
| Non-doc files changed vs `origin/main` | `implement` |
| Branch has an open PR | `pr-evidence` |
| PR is merged | `post-merge-verify` |

Every substep at an index less than the highest fired detector must be recorded done.
When the gate fails it lists the exact `advance` commands to run, which automatically
mirror approvals and progress to the GitHub issue.  To add a new observable signal,
append one entry to `OBSERVABLE_FLOOR` in `phase_state.py` — no other code changes.

When the chat approval has been given for operator gates, the agent re-runs:

```bash
python3 scripts/phase_state.py advance --branch <b> --to <gate> --operator-approved \
  [--note "<one-line summary of what was agreed>"]
```

`--operator-approved` causes `phase_state.py` to **auto-stamp the issue** on the
operator's behalf: it adds `approved:<gate>`, posts a provenance comment (including
`--note` text if supplied), and clears `awaiting:operator`.  The GitHub issue is a
read-only mirror — the operator never has to type anything there.

If the agent attempts to advance an operator gate *without* `--operator-approved`, it
posts `awaiting:operator` on the issue as an audit trail, but the refusal message
is agent-directed ("re-run with --operator-approved") — not "go to GitHub".

---

## 2. Work state — reading it

```bash
python3 scripts/phase_state.py status                  # this branch
python3 scripts/phase_state.py status --branch feat/x  # specific branch
python3 scripts/phase_state.py status --json           # machine-readable
python3 scripts/phase_state.py report                  # all open work items
python3 scripts/phase_state.py drift-check --branch b  # advisory: nudge to advance phases as work completes (obs 1; used by drain.sh)
```

Every work item has a GitHub Issue (label `jarvis-work` + `stage:*`).  The issue
body contains a substep checklist and a `<!-- phase-state -->` status block:

```
Current stage: Build (3/5) — 60% overall
Stage progress: implement [x] verify [ ] — 50%
Done: specify, setup, jam, define-evidence, plan, implement
Remaining: verify, pr-evidence, babysit, merge, post-merge-verify, retrospective
Open failures: none        Summary: verify in progress
```

### The single front door creates or links the issue
`new_requirement.py` is the only entry point for new work. It **auto-creates the
tracking issue** at kickoff — unless the operator already filed one manually.

**Link-not-create:** if the requirement text contains a GitHub issue URL
(`https://github.com/<org>/<repo>/issues/<N>`) or a `#NN` reference, the front door
detects it via `_extract_issue_ref()` and passes `--issue N` to `init_phase_tracking()`
instead of creating a new issue.  `phase_state.py init --issue N` then ensures the
existing issue has the `jarvis-work`/`stage:align` labels and the `<!-- phase-state -->`
checklist body injected (idempotent — safe to repeat).  This prevents the duplicate
`[work] …` issue that would otherwise be created alongside the manually-filed one.

After spinning up the worktree + brief + cost session it calls
`init_phase_tracking()` → `phase_state.py init --kickoff`, which seeds

**Linking an existing issue** (`phase_state.py init --branch <b> --issue <N>`) is also
idempotent and fully wires the GitHub issue: it applies `jarvis-work` + `stage:align`
labels and seeds the checklist body via `_apply_kickoff()`.  Previously, this path only
wrote the local cache and skipped the GitHub update (root cause of the #86 wiring miss,
fixed PR #86).  `phase_state.py gate` additionally fails if a tracked branch's linked
issue is missing its `stage:*` label on GitHub (drift check).

**Branch naming** — the branch is derived from the requirement text and (when known)
the GitHub issue number:

- **Link path** (`--issue N` or auto-detected `#NN` / URL): branch is `fix/i{N}-<slug>`.
  Two different issues with identical requirement text therefore always get distinct branches.
- **Create path** (no pre-filed issue): branch is `fix/<slug>`.  If that branch already
  exists locally or on `origin`, a numeric suffix (`-2`, `-3`, …) is appended automatically.
- **Explicit `--branch`** always wins and bypasses the collision logic.
- `_sanitize_requirement()` strips issue refs and common preamble phrases (e.g. "consider above
  as new requirements") from the text before slugging, so meta-instruction boilerplate does not
  dominate the slug.

The worktree base defaults to **`origin/main`** so new worktrees always start from
clean main regardless of which branch the operator is on. Pass `--base <ref>`
explicitly to inherit a different base (e.g. an in-flight framework PR).

The Cursor launcher HTML is written as a fallback only; it is not opened automatically
when the Cursor CLI succeeds. If the deeplink fails, open the `session-*-launch.html`
file in the worktree's `metrics/pr_cost/` directory manually.

The handoff deeplink pre-selects **Ask mode** + **Opus 4.8 thinking high** for the jam
phase (configurable via `new_requirement.py --mode` / `--model`). The new chat must not
inherit Agent/Auto settings from the parent window.
`done = [specify, setup]` (both are factually complete the moment the front door runs)
and prints the issue URL in the handoff banner.  So a fresh requirement shows **Align
50%** with `jam` as the current operator gate — never a misleading "0%, nothing done".
`--dry-run` prints the `gh issue create` it *would* run without touching GitHub.

### Issue hygiene
Over time, duplicate issues or issues whose PRs merged without explicit `closes #NN`
syntax can accumulate.  `scripts/issue_cleanup.py` handles periodic hygiene:

```bash
python3 scripts/issue_cleanup.py --dry-run          # show plan, no changes
python3 scripts/issue_cleanup.py --apply            # execute
python3 scripts/issue_cleanup.py --apply --issues 88,83  # restrict scope
```

Detection rules:
- **Duplicates:** two open `jarvis-work` issues share the same `Branch: ...` value in
  their bodies, or one issue's body contains `(issue #NN)` referencing an open issue.
  The `[work]`-prefixed issue (or lowest number) is the survivor; the other is closed
  with a breadcrumb comment.
- **Stale merged-PR issues:** a merged PR either uses `closes/fixes/resolves #NN` syntax
  in its body, or its `headRefName` matches the branch recorded in the issue body.
  Issues with any open PR are never closed.

### Jira/Linear migration seam
`phase_state.py` defaults to `source="github"`.  When the backend switches, update
`--source linear`/`--source jira` in `new_requirement.py`; all other callers are unchanged.

---

## 3. Category × subcategory map (automation maturity)

Automation maturity: M0 manual prose → M1 scripted → M2 gate/hook → M3 self-driving.

| Category | Subcategory | Where it lives | Current form | Maturity | Driver now | Target |
|---|---|---|---|---|---|---|
| Requirement intake | Feature ideation | operator brain | n/a | M0 | operator | L2: agent researches |
| | Req. jamming | Ask mode + WORKFLOW.md | prose | M0 | operator | L1 (this PR) |
| | Worktree spin-up | new_requirement.py | script | M1 | agent | M2 |
| | Phase tracking | phase_state.py + GitHub Issues | script | M1 | agent | M2 |
| Planning | Plan execution-readiness | plan-execution-readiness.md | checklist | M1 | agent | M2 ✓ (check_plan_readiness) |
| | Evidence definition | CONTRIBUTING.md §evidence | prose | M0 | operator+agent | M1 |
| Engineering | Behavioral anchor | .cursor/rules/ spine | always-on rule | M1 | agent | M1 |
| | Design principles | .cursor/rules/jarvis.mdc | always-on rule | M1 | agent | M1 |
| | Hard Lessons | jarvis-hard-lessons.md | on-demand rule | M1 | agent | M2 (gate conversions) |
| | 100% test coverage | pytest | gate | M2 | agent | M2 |
| Evidence & verification | Local verify harness | verify.py | gate | M2 | agent | M2 ✓ (this PR) |
| | Plan readiness gate | check_plan_readiness.py (evidence-tier declaration required) | gate | M2 | agent | M2 ✓ |
| | Evidence readiness predictor | check_evidence_readiness.py (mirrors D2a rubric; exits 1 for pytest-only) | gate | M2 | agent | M2 ✓ |
| | Lifecycle conformance | verify_lifecycle.py | gate | M2 | agent | M2 ✓ |
| | Secret scan | verify.py (diff-based) | gate | M2 | agent | M2 ✓ |
| PR lifecycle | PR description | check_pr_description.py | CI gate | M2 | agent | M2 |
| | Review replies | check_pr_review_replies.py | CI gate | M2 | agent | M2 |
| | Babysit | babysit skill | skill | M1 | agent | M2 |
| | Cost ledger | pr_cost_ledger.py | script | M1 | agent | M1 |
| | Operator merge | branch protection | gate | M2 | operator | M2 |
| Agent domain | Jarvis routing | jarvis routing card | always-on rule | M1 | agent | M1 |
| | BHAGA pipeline | bhaga* + RUNBOOK + DOMAIN | glob-scoped | M1 | agent | M1 |
| | CHITRA tax | chitra* + playbook | glob-scoped | M1 | agent | M1 |
| | AKSHAYA inventory | akshaya.md | glob-scoped | M1 | agent | M1 |
| Knowledge | PROGRESS.md | PROGRESS.md | manual | M0 | agent | M1 |
| | Retrospective | doc-maintenance + retro substep | manual | M0 | agent | M1 |
| | Cost observability | Grafana | dashboard | M1 | operator | M2 |

---

## 4. Agent hierarchy — common vs. agent-specific rules

**Rule:** anything common to all agents lives in Tier 1 (Spine) or Tier 0 (Gates);
anything specific to one agent lives in that agent's glob-scoped Tier-2 card;
reusable capability lives in `skills/`.

```
Tier 0 Gates (all agents)          Tier 1 Spine (all agents, always-on)
────────────────────────────       ──────────────────────────────────────
verify.py                          AGENTS.md (~100 lines, TOC)
check_plan_readiness.py            jarvis routing card (~60 lines)
verify_lifecycle.py                behavioral anchor (~15 lines)
CI workflows (12)                  self-drive rule (~10 lines)
git hooks: pre-commit + pre-push   consult-first (single source)
                                   pr-workflow.mdc
                                   plan-execution-readiness.md
                                   user-preferences.md

Tier 2 AGENT-SPECIFIC (glob-scoped, on-demand)
────────────────────────────────────────────
BHAGA:   bhaga-principles.md + bhaga.md + RUNBOOK + DOMAIN  → agents/bhaga/**
CHITRA:  chitra.md + chitra-playbook + chitra-workflows + demo-flow → agents/chitra/**
AKSHAYA: akshaya.md  → agents/akshaya/**
CHANAKYA: chanakya.md → agents/chanakya/**

Tier 2 PROCESS REFS (on-demand)
────────────────────────────────
CONTRIBUTING.md stub + docs/contributing/*
docs/WORKFLOW.md (this file)
jarvis-hard-lessons.md
```

### Common-rule hoist inventory
These rules were originally duplicated in agent cards.  After M5 they live only
in the Spine; agent cards have a one-line pointer.  `verify_lifecycle.py` assertion
#6 blocks re-duplication.

| Principle | Hoisted from | New home |
|---|---|---|
| Consult-before-planning | bhaga-principles + AGENTS + CONTRIBUTING | single Spine block |
| Branch → PR → review → CI → merge | bhaga-principles | CONTRIBUTING stub + pr-workflow.mdc |
| Plan-execution-readiness pointer | bhaga-principles | plan-execution-readiness.md (Spine) |
| Drive end-to-end / babysit | bhaga-principles | pr-workflow.mdc + self-drive rule |
| Keep docs in lock-step | bhaga-principles | doc-maintenance.md |
| Never retry when side effect can fire | bhaga-principles | jarvis routing card (generic rule) |
| skills ≠ agents | akshaya + jarvis + AGENTS | AGENTS.md rule 3 |
| Config-driven, no hardcoding | akshaya | AGENTS.md rule 4 |
| Use user-playwright not cursor-ide-browser | akshaya + AGENTS + jarvis | Spine convention |
| No PII / secrets in git | chitra + AGENTS + jarvis | AGENTS.md rule 2 |
| Cost discipline (generic half) | chitra | docs/contributing/cost.md |

---

## 5. Operator-reserved zone

The operator's time is **ideation, requirement jamming, evidence definition, and final merge**.
Everything else is agent-driven.

```
OPERATOR-RESERVED                     AGENT ZONE
──────────────────                    ─────────────────────────────────────────
1. Feature ideation                   2. Setup: new_requirement.py + brief + issue
3. Requirements jam (Ask mode)        5. Plan (check_plan_readiness gate)
4. Evidence definition → PR §4        6. Implement
9. Squash-merge to main               7. Verify (verify.py --full)
                                      8. PR + babysit (desc + cost + replies + CI)
                                      10. Post-merge verify + retrospective
```

The agent **self-sequences** all agent-zone phases.  `phase_state.py advance` posts an
`awaiting:operator` prompt on the issue whenever an operator gate is reached, so the
operator always knows where to input.

### Post-merge lifecycle (automated)
`pr-merged-lifecycle.yml` fires on every squash-merge to `main` and:
1. Resolves the tracking issue (phase-cache → gh issue scan).
2. Stamps `approved:merge` and advances `merge` (the squash-merge IS the operator approval).
3. Posts a cross-reference comment on the issue linking the merged PR.
4. Parses the §4 "Post-merge verification" block; runs read-only commands in CI; posts results.
5. Advances `post-merge-verify` once verification runs (or is skipped).
6. Posts a retrospective prompt (speed / cost / accuracy grading checklist) on the issue.

The **retrospective** is always agent-driven in a follow-up chat (CI cannot read local transcripts).
The agent reads the PR conversation + transcripts, **grades the cycle in a retro plan (Plan mode),
jams it with the operator**, proposes ≥1 process improvement as follow-up GitHub issues, runs
preference candidates through the user-model guardrail, then closes the tracking issue.

> **No direct `PROGRESS.md` push on the merged branch** — `check_no_main_progress_push.py` blocks it.
> Any PROGRESS entry lands via a follow-up issue / its own PR.

See `self-drive.mdc` § Retrospective protocol for the full sequence.

### Ship-emoji force-merge
When `aditya2kx` posts a standalone 🚀 or 🚢 comment on a PR,
`ship-emoji-force-merge.yml` checks:
- Is the comment author `aditya2kx` with `OWNER` association?
- Is the ONLY failing required check the Claude evidence-confidence gate (< 95%)?
- Is the Claude verdict NOT `REQUEST CHANGES`?

If all true → `gh pr merge --squash --admin`.  Hard CI checks (secret-scan, pytest,
pr-cost-gate), REQUEST CHANGES verdicts, and unreplied threads are never bypassed.
The workflow becomes active after this PR merges to `main` (issue_comment workflows
require the workflow to be on the default branch).

---

## 6. Autonomy ladder (L0 → L3)

```
L0 (before this PR):
  Operator re-prompts at every phase.  No local verify.  No phase tracking.

L1 (this PR — delivered):
  Agent self-sequences phases once requirement + evidence agreed.
  Local verify harness (verify.py) mirrors CI.
  Phase tracked in GitHub Issues (stage:* labels + status block).
  Operator only inputs at 3 gates + final merge.

L2 (roadmap):
  Brief requirement → agent researches candidate capabilities + drafts evidence.
  Operator reviews and approves; agent builds.

L3 (roadmap):
  Higher-level goal → agent researches online to propose new requirements.
  Operator reviews + prioritizes; everything else is self-driving.
```

---

## 7. Verification matrix

| Guarantee | Mechanism | Evidence type |
|---|---|---|
| New requirement → worktree + chat + phase ladder | new_requirement.py --dry-run | Script output |
| Front door is interrogation-free (no jam in parent chat) | verify_lifecycle.py assertion #9 | Conformance PASS |
| Jam handoff opens Ask mode; operator sets model (deeplink can't) | verify_lifecycle.py assertion #10 | Conformance PASS |
| Thorough plan without probing | check_plan_readiness.py | Passing score (10/10) |
| Plan gate requires jam + define-evidence recorded first | check_plan_readiness.py phase precheck; assertion #13 | Exit 1 if missing; OBSERVABLE_FLOOR plan detector |
| L1 mechanisms wired | verify_lifecycle.py | Conformance PASS |
| Phase tracking queryable | phase_state.py status/report | Status output |
| Operator gates unskippable | phase_state.py advance → nonzero | Exit code |
| Lifecycle ladder non-bypassable (whole ladder, not just jam) | verify_lifecycle.py assertion #11; phase_state.py gate (hard gate in verify --full) | Conformance PASS + Gate exit code |
| Every PR merges into `main`, never a drifted repo default branch | check_repo_default_branch.py (hard gate in verify --full) + `pr-base-branch.yml` CI workflow (fails any PR with `base.ref != main`) | Gate exit code + CI check |
| Operator preference stored only if generalizable (guardrail) | skills/user_model/guardrail.py + store.add_preference; assertion #12 | score_candidate exits 0/6 for task-specific text |
| Pre-ask consult: apply stored preference before asking | .cursor/rules/preference-consult.mdc (always-on rule) | Rule file present |
| Local loop mirrors CI | test_verify.py::test_ci_parity | Test PASS |
| Babysit unprompted | pr-workflow.mdc + babysit skill | Always-on rule |
| Review replies done | check_pr_review_replies.py | Gate exit 0 |
| Whole lifecycle works end-to-end | dogfood_lifecycle.py run/resume/check | Annotated transcript in docs/dogfood/ |

### Dogfooding the lifecycle
`scripts/dogfood_lifecycle.py` drives a trivial dummy requirement through all 12
substeps against **real** infrastructure (a real tracking issue + a throwaway PR off
`origin/main` + a real operator merge), proving the gates have teeth and the harness
advances correctly.  It pauses at the operator-reserved `merge` gate:

```bash
python3 scripts/dogfood_lifecycle.py run        # walks 8 agent substeps + 2 gate demos, opens the dummy PR, pauses at merge
# operator approves + squash-merges the dummy PR, then:
python3 scripts/dogfood_lifecycle.py resume     # merge + post-merge-verify + retrospective; writes the transcript
python3 scripts/dogfood_lifecycle.py check      # offline conformance assertions on the recorded run
```

The latest run's annotated transcript lives in `docs/dogfood/lifecycle-run-<date>.md`.

---

## 8. Local event-driven dev lifecycle (v2, PR #101)

### Overview

v2 replaces cloud agents with a **local-first, event-driven** flow:
- GitHub Actions emit cheap **`jarvis-signal` comments** on tracking issues (zero AI token cost).
- A laptop listener (`dev_event_listener.py`) catches up on signals when the Mac is online.
- Signals are routed to the correct **worktree inbox** (`session-<slug>-pending.jsonl`) via `dev_event_router.py`.
- Local Cursor chats pick up events via `.cursor/hooks.json` (busy/idle lock + queue drain) or explicit catch-up.

### Event path

```
GH Action (check_suite / issue_comment / pr-merged-lifecycle)
  → jarvis-signal comment on tracking issue
  → dev_event_listener catch-up (reads comments since last_signal_cursor)
  → dev_event_router.route_signal (dedupe, debounce, branch lookup)
  → session-<slug>-pending.jsonl (FIFO inbox)
  → Cursor hooks / catch-up drain → agent handles event
```

### Event → kind mapping

| Signal event | Router kind | Trigger | Notes |
|---|---|---|---|
| `ci_failed` | `babysit_ci` | `check_suite.completed` failure | Debounced (5 min) |
| `ci_passed` | `ci_green` | `check_suite.completed` success | — |
| `ci_other` | `ci_status` | `check_suite.completed` other | — |
| `pr_merged` | `retrospective` | `pr-merged-lifecycle.yml` | Triggers retro jam flow |
| `intake` | `intake` | `/jarvis-new-task` comment | Allowlist-gated; no debounce. The signal's `issue` field is threaded through to `new_requirement.py --issue N`, which fetches the issue's title+body (`gh issue view`) to seed the brief — a short intake comment alone (e.g. "let's work on this") is not enough context — and links the EXISTING issue instead of creating a duplicate. Branch is `fix/i{N}-<title-slug>` (issue-based, unique). |
| `comment` | `address_comment` | Operator comment on any issue/PR | Allowlist-gated (workflow primary gate); loop-safe. **Note:** for PR comments, branch is resolved via `gh pr view --json headRefName` (no phase cache needed). For plain issue comments, branch is resolved via `find-branch` (reads laptop phase cache); absent on the Actions runner → gracefully skips with log message. |
| _(PR→issue link)_ | _(GH-side, no inbox)_ | `pull_request.opened` → `pr-issue-link` job in `jarvis-dev-signals.yml` | obs 3: comments the PR URL on the tracking issue + appends `Refs #N` to the PR body (both idempotent). Resolves the issue via `find-issue --branch` (gh scan fallback works in CI). Active once merged to `main`. |

### New scripts

| Script | Role |
|---|---|
| `scripts/dev_event_router.py` | Parse signals, idempotency, debounce, write inbox, update phase cache. **Inbox routing (obs 4b):** the pending/processed inbox is written to the **child worktree's** `metrics/pr_cost/` (from `cache["worktree_path"]`) so the child's `drain.sh` actually sees it — the daemon-side phase cache + `delivered_signals` dedup stay in the daemon repo. Falls back to the module dir when no worktree is recorded. |
| `scripts/dev_event_listener.py` | `catch-up`, `watch`, `dispatch`; macOS auto-open/focus worktree (osascript + `open -a Cursor` fallback; `LOCAL_EVENT_AUTO_OPEN`). GH API → `parse_signal` → `route_signal` → `pending.jsonl` write proven via non-dry-run catch-up run. |
| `scripts/check_no_main_progress_push.py` | Mechanical guard: block PROGRESS.md direct push to main |

### Signal format

```html
<!-- jarvis-signal:{"id":"<uuid>","event":"ci_failed","branch":"fix/…","pr":109,"ts":"…"} -->
```

Human-readable summary above; machine block in HTML comment (greppable, idempotent by UUID).

### Phase cache v2 schema (new fields)

```json
{ "worktree_path": "/path/to/jarvis-wt-…", "last_signal_cursor": "ISO",
  "delivered_signals": ["uuid-1"], "pending_event_count": 0 }
```

`phase_state.py status` and `report` print `Worktree:` and `Pending events:`.

### Cursor hooks (`.cursor/hooks.json`)

| Hook | Script | Behavior |
|---|---|---|
| `beforeSubmitPrompt` | `mark_busy.sh` | Write `state=busy` + heartbeat to status lock |
| `stop` | `drain.sh` | Mark idle; if `LOCAL_EVENT_AUTO_DISPATCH≠0` and inbox non-empty, pop oldest event and return `followup_message` (warm zero-click drain). Real output: `{"followup_message": "[AUTO-DISPATCH] New requirement intake signal received.\n\nEvent: {…}\n\nRun: python3 scripts/new_requirement.py …"}`. When `LOCAL_EVENT_AUTO_DISPATCH=0`, returns `{}` (no auto-dispatch). **Phase-drift nudge (obs 1):** when the inbox is empty, runs `phase_state.py drift-check` and, if observable progress has outrun the recorded `done` list, returns a `followup_message` listing the exact `advance` commands so phases are recorded per-substep instead of batched at PR time. |
| `sessionStart` | `announce_pending.sh` | Surface pending event count as context |

### Feature flags

| Flag | Default | Purpose |
|---|---|---|
| `LOCAL_EVENT_AUTO_OPEN` | on | Opens/focuses worktree Cursor window on signal delivery |
| `LOCAL_EVENT_AUTO_DISPATCH` | on | Seeds drain prompt to auto-start agent on actionable events; non-preemptive |
| `LOCAL_EVENT_WEBHOOK` | off | HTTP push endpoint (v2.1, deferred) |

### `new_requirement.py` changes (H2)

After creating a worktree, posts a comment on the tracking issue with the worktree path and branch so it's visible on GitHub. Also writes `worktree_path` into both the parent and worktree phase caches.

### `check_no_main_progress_push.py`

Wired as a `verify.py` gate (`progress-push-guard`). Blocks pushes that target `refs/heads/main` and include `PROGRESS.md`. On feature branches always exits 0 (PROGRESS via PR is the sanctioned path).

### Workflows

| Workflow | Change |
|---|---|
| `pr-merged-lifecycle.yml` | Added `Emit pr_merged signal` step; retrospective prompt now instructs jam→plan→issues flow (no direct `PROGRESS.md` write). **Fixed (2026-07-01):** the file was invalid YAML from #85 onward (column-0 Python heredocs + a multi-line `--body` broke the `run:` block scalars) so the post-merge job silently never ran — every merge since #85 stranded its issue (no merge-advance / PR-link / post-merge-verify / retro). Re-indented the mis-authored spans into their block scalars (byte-identical content); now parses + `bash -n` + `py_compile` clean. |
| `jarvis-dev-signals.yml` | `check_suite` → CI signals; `issue_comment` → `intake-signal` (/jarvis-new-task) + `comment-signal` (operator comments, loop-safe); `pull_request.opened` → `pr-issue-link` (obs 3: link PR↔issue, idempotent); label-gated `pull_request` → pre-merge evidence. **Note:** `issue_comment` and `check_suite` jobs only activate once the workflow lands on `main` (GitHub resolves those triggers from the default branch). `comment-signal` end-to-end proven pre-merge by temporarily setting the PR branch as default branch — run [28486518592](https://github.com/aditya2kx/jarvis/actions/runs/28486518592) ✅. |

## 9. Deferred roadmap (out of scope for this PR)

- Jira/Linear backend for phase_state.py (only `source` seam built now)
- Agent researching candidate capabilities from a brief requirement (L2)
- Agent researching online to propose new requirements (L3)
- Slack → requirement auto-intake
- Local LLM pre-review
- Converting remaining Hard Lessons to mechanical gates
- Agent/skill scaffolder
