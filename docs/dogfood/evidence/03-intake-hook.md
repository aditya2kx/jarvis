# Evidence: Deterministic Intake Hook (PR #74)

Date: 2026-06-25

## M1 — Hook fires deterministically

### Test 1 — Block on new-requirement phrase

```
$ echo '{"prompt":"I want to work on a new requirement: add multi-date Slack command support"}' \
    | CURSOR_PROJECT_DIR=$(pwd) python3 .cursor/hooks/prompt_gate.py
```

Output:
```json
{
  "continue": false,
  "user_message": "New-requirement intake gate fired.\n\nThis prompt looks like a new, separate requirement...\n\n    python3 scripts/new_requirement.py --requirement \"I want to work on a new requirement: add multi-date Slack command support\"\n..."
}
```

**Result: BLOCK — `continue: false` with instruction message.**

### Test 2 — Pass-through on `//inline` override

```
$ echo '{"prompt":"//inline keep going with the current bug fix"}' \
    | CURSOR_PROJECT_DIR=$(pwd) python3 .cursor/hooks/prompt_gate.py
```

Output:
```json
{"continue": true}
```

**Result: PASS-THROUGH — `continue: true`.**

### Test 3 — Corpus grows on every call

```
$ python3 skills/user_model/store.py corpus-tail --limit 3
```

Output (last 3 entries):
```
{"ts": "2026-06-25T16:04:59-0500", "source": "cli-test", "text": "test corpus append from CLI", ...}
{"ts": "2026-06-25T16:08:51-0500", "source": "cursor-hook", "text": "I want to work on a new requirement: add multi-date Slack command support", ...}
{"ts": "2026-06-25T16:08:56-0500", "source": "cursor-hook", "text": "//inline keep going with the current bug fix", ...}
```

**Result: Both prompts appended automatically — corpus grew by 2 entries.**

## M2 — Harness locked in conformance

```
$ python3 scripts/verify_lifecycle.py --assert 18
```

Output:
```
✓ 18  intake hook harness wired: enforce.sh + prompt_gate.py + dispatcher + corpus-append CLI  PASS
```

```
$ python3 -m pytest scripts/test_verify_lifecycle.py -k 18 -v
```

Output: `5 passed`

**Result: A18 PASS. 5/5 unit tests pass.**

## M3 — Full suite

```
$ python3 scripts/verify_lifecycle.py
Passed: 18  Warn (pre-milestone): 0  Failed: 0
Conformance PASSED.

$ python3 -m pytest scripts/test_verify_lifecycle.py
51 passed
```

**Result: 18/18 assertions pass. 51/51 unit tests pass.**

## Portability

The enforcement script `.cursor/hooks/enforce.sh` is repo-versioned and travels with every branch/worktree.  The one-time per-laptop dispatcher in `~/.cursor/hooks.json` is installed by:

```
bash scripts/install-git-hooks.sh
```

(Idempotent — already-present entries are skipped.)

## Override

A false-positive can be bypassed by prefixing with `//inline`:

```
//inline <original message>
```

The hook passes the full string (with prefix) through to the agent.
