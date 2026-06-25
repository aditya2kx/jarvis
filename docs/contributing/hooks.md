# Cursor Hook Authoring Guide

Cursor hooks allow repo-versioned code to run on agent events (e.g. before every user prompt).  They complement `.mdc` rules: rules are advisory prose; hooks are deterministic code.

## How the intake hook harness works

```
User prompt
    │
    ▼
~/.cursor/hooks.json          (per-laptop, one-time — installed by scripts/install-git-hooks.sh)
    │  beforeSubmitPrompt
    ▼
$CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh   (repo-versioned, travels with branch)
    │  exec python3
    ▼
.cursor/hooks/prompt_gate.py
    ├── always: append_to_corpus(prompt)
    └── if new-requirement phrase detected (and no //inline prefix):
            → {"continue": false, "user_message": "<instruct to run new_requirement.py>"}
        else:
            → {"continue": true}
```

## Output contract (`beforeSubmitPrompt`)

The only supported output is:

```json
{"continue": bool, "user_message": "<optional string shown to user>"}
```

- `continue: false` blocks the turn.  The `user_message` is shown to the operator.
- `continue: true` passes the prompt through; `user_message` is ignored.

The hook **cannot** inject context, rewrite the prompt, or message the agent.

## Portability design

| Scope | Mechanism | Installed by |
|---|---|---|
| Per-laptop, once | `~/.cursor/hooks.json` dispatcher | `scripts/install-git-hooks.sh` |
| Per-repo, versioned | `.cursor/hooks/enforce.sh` + `prompt_gate.py` | Git (travels with branch) |

The dispatcher is a single entry that calls `enforce.sh` only when `$CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh` is present and executable, so non-Jarvis repos are unaffected.

## One-time setup (per laptop)

```bash
bash scripts/install-git-hooks.sh
```

This is idempotent.  It merges the dispatcher entry into `~/.cursor/hooks.json` without overwriting existing entries.

## Override (per prompt)

To bypass the intake check on a false positive, prefix your message with `//inline`:

```
//inline keep going with the bug fix
```

The `//inline` prefix remains visible to the agent — treat it as metadata, not content.

## Adding a new hook

1. Add your script to `.cursor/hooks/` (Python preferred; no external deps beyond stdlib).
2. Call it from `enforce.sh` or create a new shell wrapper (also in `.cursor/hooks/`).
3. Update `scripts/install-git-hooks.sh` to register the hook event in `~/.cursor/hooks.json`.
4. Add a `verify_lifecycle` assertion (A-next) that checks the new hook is wired and executable.
5. Add unit tests in `scripts/test_verify_lifecycle.py`.
6. Update this file.

## Fail-open policy

Hook scripts **must** fail open (emit `{"continue": true}`) on any unexpected error.  Only emit `continue: false` on positive detection — never on infrastructure errors, import failures, or missing deps.  This prevents a broken hook from silently swallowing user prompts.

## See also

- `.cursor/hooks/prompt_gate.py` — intake gate implementation
- `.cursor/hooks/enforce.sh` — thin wrapper
- `scripts/install-git-hooks.sh` — installer
- `docs/contributing/rules.md` — when to use a rule vs. a hook
- `scripts/verify_lifecycle.py::assert_18` — conformance assertion
