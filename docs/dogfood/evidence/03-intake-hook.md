# Evidence: /jarvis-new-task Skill (PR #74)

Date: 2026-06-25

## What changed (hook → skill pivot)

The `beforeSubmitPrompt` blocking hook (`prompt_gate.py` / `enforce.sh`) was removed after repeated false positives — meta-discussion messages containing intake phrases ("not asking for a new requirement", "go ahead with new requirement setup") were blocked. The operator knows best when something is a new requirement.

Replaced with an explicit, operator-invoked `/jarvis-new-task` Cursor Skill:
- `.cursor/skills/jarvis-new-task/SKILL.md` — `disable-model-invocation: true`, runs `new_requirement.py`
- No heuristic detection, no blocking, zero false positives
- `scripts/install-git-hooks.sh` now prunes the legacy dispatcher from `~/.cursor/hooks.json`

## M1 — Skill wired, hook removed

### verify_lifecycle A18 (skill)
```
$ python3 scripts/verify_lifecycle.py --assert 18
✓ 18  jarvis-new-task skill wired: SKILL.md + disable-model-invocation + new_requirement.py  PASS
```

### Full conformance
```
$ python3 scripts/verify_lifecycle.py
Passed: 18  Warn (pre-milestone): 0  Failed: 0
Conformance PASSED.
```

### Unit tests
```
$ python3 -m pytest scripts/test_verify_lifecycle.py scripts/test_new_requirement.py -q
59 passed
```

### Hook dispatcher pruned
```
$ bash scripts/install-git-hooks.sh
installed: core.hooksPath=scripts/git-hooks
  pre-commit : capture-review -> BQ  (bypass: PR_COST_HOOK=0)
  pre-push   : verify.py --full      (bypass: VERIFY=0)
removed legacy jarvis dispatcher from /Users/.../.cursor/hooks.json
```

After re-opening the verification worktree, no messages are blocked.

## M2 — Behavioral: operator live test (pending)

After this PR is pushed, the verification worktree (`demo/intake-rule-test`) will be reset to this branch.

Expected results:
- Typing `/jarvis-new-task add multi-date Slack command support` → agent runs `new_requirement.py`, new worktree opens
- Typing any message containing "new requirement" in a meta context (e.g. "not asking for a new requirement") → passes through unblocked

Operator screenshot/transcript to be captured here before merge.
