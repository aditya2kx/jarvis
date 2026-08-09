---
name: jarvis-new-task
description: Spin up a Cursor Cloud Agent (default) or local worktree for a new requirement. Invoke explicitly with the requirement text. First member of the /jarvis-* skill family.
disable-model-invocation: true
---

# jarvis-new-task

Use this skill when the operator signals a new, separate requirement that should live on its own branch and tracking issue.

## How to invoke

Type `/jarvis-new-task` followed by the requirement text, e.g.:

```
/jarvis-new-task add multi-date support to the Slack command
```

## What to do

1. Take the text the operator typed after `/jarvis-new-task` as the requirement string.
2. If no text was provided, ask the operator for a one-line requirement description, then proceed.
3. Run the front door script verbatim:

```bash
python3 scripts/new_requirement.py --requirement "<operator's text>"
```

**Default is cloud-primary** (Issue #228): the script creates/links the tracking issue, ensures a remote branch, and spawns a **Cursor Cloud Agent**. It comments the agent URL on the issue. Pass `--local` only for dogfood/lifecycle tests that need a sibling worktree.

**Link-not-create:** if the operator includes a GitHub issue URL or `#NN` reference in
the requirement text (e.g. `/jarvis-new-task fix the auth bug #42`), the script
auto-detects it and links the existing issue instead of creating a new one.  Pass it
through verbatim — do **not** ask a clarifying question about the issue number.

**Unique branch per issue:** when `#NN` or a URL is detected, the branch is named
`fix/i{N}-<slug>`, guaranteeing that two different issues never share a branch even if
the requirement text is identical.  If no issue number is present the branch is
`fix/<slug>` with an automatic `-2`, `-3`, … suffix on collision.

4. After the script launches, **stop**. Do NOT continue implementing in the current chat.
5. Confirm to the operator that a Cloud Agent was started (or a local worktree if `--local`) and that they should continue there — open the agent URL from the tracking issue comment.

## Hard rules

- NEVER implement the requirement inline in the current chat or branch.
- NEVER ask clarifying questions before running the script — the front door accepts rough text; refinement happens in the jam gate of the new chat space (Cloud Agent).
- This is a one-shot action: run the script and stop.

## See also

- `scripts/new_requirement.py` — the front door script
- `scripts/spawn_cloud_agent.py` — Cloud Agents API helper
- `.cursor/rules/new-requirement-intake.mdc` — canonical intake rule
- Other `/jarvis-*` skills surface by typing `/jarvis` in the chat input
