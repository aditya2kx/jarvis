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
When the chat approval has been given, the agent re-runs:

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

### The single front door creates the issue
`new_requirement.py` is the only entry point for new work, and it **auto-creates the
tracking issue** at kickoff: after spinning up the worktree + brief + cost session it
calls its own `init_phase_tracking()` → `phase_state.py init --kickoff`, which seeds
`done = [specify, setup]` (both are factually complete the moment the front door runs)
and prints the issue URL in the handoff banner.  So a fresh requirement shows **Align
50%** with `jam` as the current operator gate — never a misleading "0%, nothing done".
`--dry-run` prints the `gh issue create` it *would* run without touching GitHub.

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
| | Design principles | .cursor/rules/jarvis.md | always-on rule | M1 | agent | M1 |
| | Hard Lessons | jarvis-hard-lessons.md | on-demand rule | M1 | agent | M2 (gate conversions) |
| | 100% test coverage | pytest | gate | M2 | agent | M2 |
| Evidence & verification | Local verify harness | verify.py | gate | M2 | agent | M2 ✓ (this PR) |
| | Plan readiness gate | check_plan_readiness.py | gate | M2 | agent | M2 ✓ |
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
| Thorough plan without probing | check_plan_readiness.py | Passing score (10/10) |
| L1 mechanisms wired | verify_lifecycle.py | Conformance PASS |
| Phase tracking queryable | phase_state.py status/report | Status output |
| Operator gates unskippable | phase_state.py advance → nonzero | Exit code |
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

## 8. Deferred roadmap (out of scope for this PR)

- Jira/Linear backend for phase_state.py (only `source` seam built now)
- Agent researching candidate capabilities from a brief requirement (L2)
- Agent researching online to propose new requirements (L3)
- Slack → requirement auto-intake
- Local LLM pre-review
- Converting remaining Hard Lessons to mechanical gates
- Agent/skill scaffolder
