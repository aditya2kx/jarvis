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

## Dispatcher command (stored in `~/.cursor/hooks.json`)

```
bash "$CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh"
```

With `failClosed: false`, a non-zero exit (script absent in non-Jarvis repos) is treated as `continue: true` by Cursor — no quoting issues, no inline JSON emission needed.

Verified via:
```
$ export CURSOR_PROJECT_DIR=$(pwd)
$ echo '{"prompt":"I want to work on a new requirement: X"}' \
    | bash "$CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh"
→ {"continue": false, "user_message": "New-requirement intake gate fired. ..."}

$ echo '{"prompt":"//inline keep going"}' \
    | bash "$CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh"
→ {"continue": true}
```

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

$ python3 -m pytest scripts/test_verify_lifecycle.py scripts/test_prompt_gate.py scripts/test_new_requirement.py
51 + 33 + 7 = 91 passed
```

**Result: 18/18 assertions pass. 91 unit tests pass.**

## Behavioral unit test suite for prompt_gate

```
$ python3 -m pytest scripts/test_prompt_gate.py -v
scripts/test_prompt_gate.py::TestIntakePhraseDetection::test_add_a_requirement PASSED
scripts/test_prompt_gate.py::TestIntakePhraseDetection::test_new_requirement PASSED
... (15 phrase tests)
scripts/test_prompt_gate.py::TestNearMissNegatives::test_not_renewal PASSED
scripts/test_prompt_gate.py::TestNearMissNegatives::test_word_boundary_no_new PASSED
... (7 near-miss negatives)
scripts/test_prompt_gate.py::TestInlineOverride::test_inline_bypasses_intake PASSED
scripts/test_prompt_gate.py::TestMainFunction::test_block_on_intake_phrase PASSED
scripts/test_prompt_gate.py::TestMainFunction::test_corpus_append_called PASSED
scripts/test_prompt_gate.py::TestMainFunction::test_inline_bypass_blocks_detection PASSED
scripts/test_prompt_gate.py::TestMainFunction::test_malformed_json_failopen PASSED
scripts/test_prompt_gate.py::TestMainFunction::test_missing_enforce_sh_passthrough PASSED
scripts/test_prompt_gate.py::TestMainFunction::test_no_workspace_passthrough PASSED
scripts/test_prompt_gate.py::TestMainFunction::test_passthrough_normal_prompt PASSED
33 passed
```

## Seed-cache behavioral test (A17 proof)

```
$ python3 -m pytest scripts/test_new_requirement.py::TestSeedCacheToWorktree -v
scripts/test_new_requirement.py::TestSeedCacheToWorktree::test_copies_cache_to_worktree PASSED
scripts/test_new_requirement.py::TestSeedCacheToWorktree::test_no_op_if_source_missing PASSED
2 passed
```

The `test_copies_cache_to_worktree` test creates a real phase cache in `metrics/pr_cost/`, calls `_seed_cache_to_worktree()`, and asserts the file is present in the worktree — proving the `Issue: #none` bug is fixed.

## Dispatcher idempotency

```
$ bash scripts/install-git-hooks.sh | grep "cursor hook"
cursor hook dispatcher: already present — skipped
  cursor hook : beforeSubmitPrompt dispatcher -> ~/.cursor/hooks.json
```

Second run shows "already present — skipped" — idempotent confirmed.

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
