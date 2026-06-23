# Local development loop

## The verify harness

`scripts/verify.py` is the local CI mirror.  It runs the same gates CI runs so you
catch failures before pushing.

```bash
python3 scripts/verify.py --fast     # pre-commit: secret scan + doc-freshness + changed tests
python3 scripts/verify.py --full     # pre-push: full pytest + PR gates (desc, replies)
python3 scripts/verify.py --full --plan path/to/plan.md  # also check plan readiness
python3 scripts/verify.py --full --strict  # promote doc-freshness to HARD
```

**Exit 0 = all hard gates passed.  Exit 1 = something is broken.**

The pre-push hook (`scripts/git-hooks/pre-push`) runs `--full` automatically.
Bypass: `VERIFY=0 git push --no-verify` (only after you've confirmed the diff is clean).

## Gate registry

| Gate | Mode | Hard? | What it checks |
|---|---|---|---|
| secret-scan-staged | fast | HARD | Staged diff for credentials |
| doc-freshness | fast | nudge | Code↔doc couplings |
| pytest-changed | fast | HARD | Test files for changed scripts |
| secret-scan-full | full | HARD | Full diff since origin/main |
| doc-freshness-base | full | nudge (--strict = HARD) | Full branch couplings |
| pytest-full | full | HARD | All test suites |
| plan-readiness | full | HARD | 10-point plan checklist |
| pr-description | full | HARD | PR §4 template sections |
| pr-review-replies | full | HARD | Every inline comment replied to |

CI-parity test in `scripts/test_verify.py::test_ci_parity` ensures the gate set
stays in sync with CI workflows.

## Sub-agent policy (narrow scope)
Use sub-agents (`Task` tool with `subagent_type="explore"` or `"generalPurpose"`)
only for **read-only** context gathering — exploring a codebase, answering a question.
Never use a swarm of parallel agents for code that writes to prod or has side effects.
Sub-agents are context firewalls, not execution environments.

## Starting new work (the single front door)
```bash
python3 scripts/new_requirement.py --requirement "Add X"            # worktree + brief + cost session + tracking issue
python3 scripts/new_requirement.py --requirement "Add X" --dry-run  # preview; prints the gh issue create it would run
```
`new_requirement.py` auto-creates the GitHub tracking issue at kickoff
(`init_phase_tracking()` → `phase_state.py init --kickoff`), seeding `specify` +
`setup` as done so the issue opens at **Align 50%** with `jam` as the next operator
gate.  The issue URL is printed in the handoff banner.

## Phase state tracking
```bash
python3 scripts/phase_state.py status              # current stage / % / remaining
python3 scripts/phase_state.py report              # all open work items
python3 scripts/phase_state.py advance --branch <b> --to <substep>
```
See `scripts/phase_state.py --help` for all sub-commands.

## Dogfooding the full lifecycle
```bash
python3 scripts/dogfood_lifecycle.py run     # walk a dummy requirement through all 12 substeps; pause at merge
python3 scripts/dogfood_lifecycle.py resume  # after you merge the dummy PR: finish + write the transcript
python3 scripts/dogfood_lifecycle.py check   # offline conformance assertions on the recorded run
```
The annotated transcript lands in `docs/dogfood/lifecycle-run-<date>.md`.
