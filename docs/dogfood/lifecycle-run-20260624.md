# Dogfood lifecycle run — 2026-06-24 (genuine flow + live operator verification)

**Requirement:** Add `docs/dogfood/README.md` documenting the dogfood evidence directory.  
**Branch:** `fix/add-docs-dogfood-readme-md-documenting`  
**Tracking issue:** [#66](https://github.com/aditya2kx/jarvis/issues/66) (closed)  
**Throwaway PR:** [#67](https://github.com/aditya2kx/jarvis/pull/67) (closed unmerged — live verification superseded merge)  
**Framework PR:** [#63](https://github.com/aditya2kx/jarvis/pull/63) (harness-engineering redesign)

This transcript records a **genuine-flow** dogfood run (real front door, Ask-mode jam subagent,
Opus 4.8 plan, clean PR opened and babysat to green) followed by **live operator verification**
that replaced merging the throwaway PR.

Evidence bundle:
- [Ask-mode jam transcript](evidence/01-jam-ask-mode.md)
- [Opus 4.8 plan — 10/10 readiness](evidence/02-plan-opus.md)

---

## Substep ledger

| # | Substep | Driver | Marker | Outcome |
|---|---|---|---|---|
| 1 | specify | operator | SEEDED | Requirement supplied at `new_requirement.py --no-open` |
| 2 | setup | agent | SEEDED | Worktree + issue #66 created by front door |
| 3 | jam | operator | OPERATOR-REAL | Ask-mode subagent; approved in chat → auto-stamped to issue (single-surface M1) |
| 4 | define-evidence | operator | OPERATOR-REAL | §4 contract in `evidence/01-jam-ask-mode.md`; approved in chat |
| 5 | plan | agent | HARNESS-DRIVEN | Opus 4.8 plan scored 10/10 (`check_plan_readiness.py`) |
| 6 | implement | agent | HARNESS-DRIVEN | `docs/dogfood/README.md` + `PROGRESS.md` in dummy worktree |
| 7 | verify | agent | HARNESS-DRIVEN | `verify.py --fast` PASS (harness scripts run from PR #63 repo root) |
| 8 | pr-evidence | agent | HARNESS-DRIVEN | PR #67 opened; 6-section template + evidence links |
| 9 | babysit | agent | HARNESS-DRIVEN | CI green; Claude FULL review (no RE-REVIEW misfire); cost gate PASS ($0.47 build) |
| 10 | merge | operator | **OPERATOR-REAL** | **PR #67 closed unmerged** — operator chose live verification instead of squash-merge |
| 11 | post-merge-verify | agent | HARNESS-DRIVEN | N/A (PR not merged); live verification substituted (see below) |
| 12 | retrospective | agent | HARNESS-DRIVEN | This transcript + assertion #9 on PR #63 |

---

## Live operator verification (supersedes PR #67 merge)

After PR #67 reached merge-ready state, the operator reviewed the genuine-flow evidence and
continued testing in the parent chat rather than merging the throwaway doc PR. Findings:

### What worked

1. **Single-surface operator gates (M1):** Jam and define-evidence approvals given in chat;
   `phase_state.py advance --operator-approved --note` auto-stamped issue #66 — no GitHub typing.
2. **Genuine-flow evidence:** Ask-mode jam subagent + Opus 4.8 plan (10/10) captured in
   `docs/dogfood/evidence/`.
3. **RE-REVIEW fix:** First Claude review on PR #67 was a FULL review (Part B corroborated).
4. **Front door + tracking:** `new_requirement.py` created worktree, brief, issue #66 at Align 50%.

### Gaps found (live)

| ID | Finding | Fix landed on PR #63 |
|---|---|---|
| FB-1 | Parent chat agent jammed (asked clarifying questions) instead of firing `new_requirement.py` immediately when operator shared a rough requirement | **Assertion #9** in `verify_lifecycle.py`: front door is interrogation-free (no `input()`; vague text accepted). Mechanical check, not prose. |
| — | Operator rejected prose-only fix for FB-1; wanted harness/mechanical enforcement per PR #63 thesis | Plan revised; assertion #9 implemented (9/9 conformance PASS) |
| — | Dogfood run used `--no-open` (no new Cursor window); operator expects normal front door to open new chatspace for real requirements | Documented: `--no-open` is headless/agent-only; operator requirements use default (opens Cursor window) |

### Operator Q&A (evidence of review)

The operator reviewed the run by asking:
1. What was the new feature? → `docs/dogfood/README.md` (trivial doc to exercise all 12 substeps)
2. New chatspace/worktree? → Worktree at `jarvis-wt-fix-add-docs-dogfood-readme-md-documenting`; `--no-open` meant no separate Cursor window (dogfood convention)
3. Jam transcript? → `docs/dogfood/evidence/01-jam-ask-mode.md`
4. Plan before execution? → `docs/dogfood/evidence/02-plan-opus.md` (10/10)

---

## Cleanup

- PR #67: closed unmerged (2026-06-24)
- Issue #66: closed (2026-06-24)
- Dummy worktree/branch: removed after close

---

## Conformance

```bash
$ python3 scripts/verify_lifecycle.py
Passed: 9  Warn (pre-milestone): 0  Failed: 0
Conformance PASSED.
```

Assertion #9 encodes the intake contract discovered during live verification.
