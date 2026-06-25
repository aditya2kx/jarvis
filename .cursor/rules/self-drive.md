---
description: Self-drive rule — agent self-sequences development phases without operator reminders. Always-on for all agents.
alwaysApply: true
---

# Self-drive rule

**You drive every agent-zone phase autonomously.  You pause only at operator-reserved gates.**

After each agent substep's exit criterion is met:
```bash
python3 scripts/phase_state.py advance --branch <branch> --to <next-substep>
```

The 5 tracking stages and 12 substeps are in `scripts/lifecycle.py`.
Operator-reserved gates (where you MUST pause and await approval):
- `specify` — requirement stated
- `jam` — requirements agreed
- `define-evidence` — PR §4 contract approved
- `merge` — operator squash-merges

At each operator gate, `phase_state.py advance` will refuse and post an `awaiting:operator` prompt on
the GitHub issue.  Do not attempt to self-advance past these gates.

**You do NOT need the operator to remind you to:**
- Set up a worktree and kickoff brief (run `new_requirement.py`)
- Make the plan thorough (apply `check_plan_readiness.py` yourself)
- Run `verify.py --full` before pushing
- Create/update the PR, populate §4 evidence, and babysit to green
- Advance phase state after each completed substep
- Verify post-merge state and write the retrospective to `PROGRESS.md`

If a phase is taking longer than expected, fail it: `phase_state.py fail --branch <b> --reason "..."`.
