---
name: jarvis-new-task
description: Spin up an isolated worktree, branch, tracking issue, and brief for a new requirement. Invoke explicitly with the requirement text. First member of the /jarvis-* skill family.
disable-model-invocation: true
---

# jarvis-new-task

Use this skill only when the operator explicitly asks for a new task / worktree (typing `/jarvis-new-task`). Otherwise follow-up requests continue in the current worktree — see `new-requirement-intake.mdc`.

## How to invoke

Type `/jarvis-new-task` followed by the requirement text, e.g.:

```
/jarvis-new-task add multi-date support to the Slack command
```

## What to do

1. Take the text the operator typed after `/jarvis-new-task` as the requirement string.
2. If no text was provided, ask the operator for a one-line requirement description, then proceed.
3. Run the front door script verbatim. It picks where the task runs from the surface you are on
   (a Mac chat → Mac worktree via `scripts/new_requirement.py`; a Cloud Agent chat → a new Cloud Agent);
   the operator can override with a first word `mac` or `cloud`:

```bash
python3 scripts/jarvis_task.py "<operator's text>"
```

The same command works as a GitHub issue/PR comment: `/jarvis-new-task <text>` starts a Cloud Agent,
`/jarvis-new-task mac <text>` queues it for the Mac. `/jarvis-cloud-task` is a deprecated alias.

**Link-not-create:** if the operator includes a GitHub issue URL or `#NN` reference in
the requirement text (e.g. `/jarvis-new-task fix the auth bug #42`), the script
auto-detects it and links the existing issue instead of creating a new one.  Pass it
through verbatim — do **not** ask a clarifying question about the issue number.

**Unique branch per issue:** when `#NN` or a URL is detected, the branch is named
`fix/i{N}-<slug>`, guaranteeing that two different issues never share a branch even if
the requirement text is identical.  If no issue number is present the branch is
`fix/<slug>` with an automatic `-2`, `-3`, … suffix on collision.

4. After the script runs, read its stdout:
   - If it prints `CONTINUE_IN_THIS_CHAT=1` (Agents Window / Glass attach): **do not stop**.
     The worktree was created for *this* chat. `cd` to the printed Worktree path, read the
     Brief, and continue the jam in this conversation (restate requirement + §4). Do **not**
     open another Cursor window.
   - Otherwise (new window handoff): **stop**. Confirm the new worktree/window and tell the
     operator to continue there. Do NOT implement in the current chat.
5. Cloud path: issue is opened / commented; agent link appears on the issue — stop after confirming.

## Hard rules

- NEVER implement the requirement inline in the *calling* chat when a new window was opened.
- When `CONTINUE_IN_THIS_CHAT=1`, this chat *is* the new task space — continue the jam here.
- NEVER ask clarifying questions before running the script — the front door accepts rough text; refinement happens in the jam gate of the new chat space.
- Pass `--new-window` only when the operator explicitly wants a separate Cursor window from Glass.

## See also

- `scripts/jarvis_task.py` — the router; `scripts/new_requirement.py` — the Mac front door it calls
- `.cursor/rules/new-requirement-intake.mdc` — canonical intake rule
- Other `/jarvis-*` skills surface by typing `/jarvis` in the chat input
