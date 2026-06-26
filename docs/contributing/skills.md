# Jarvis Skills — the `/jarvis-*` family

Cursor Skills (`.cursor/skills/<name>/SKILL.md`) are the canonical way to ship
**explicitly-invoked, git-versioned commands** in this repo.  They travel with
every branch, surface in Cursor's `/` command palette, and give the operator
full control over when they fire — no heuristics, no false positives.

---

## Naming convention

All Jarvis skills use the `jarvis-` prefix so that typing `/jarvis` in Cursor's
chat input surfaces the whole family in the autocomplete:

```
/jarvis-new-task      — spin up an isolated worktree for a new requirement
```

Future members follow the same pattern:

```
/jarvis-cost-report   — pull current PR cost from BigQuery
/jarvis-phase-status  — show phase-state ladder for this worktree
```

---

## How to add a new `/jarvis-*` skill

1. Create `.cursor/skills/jarvis-<slug>/SKILL.md`.
2. Use this frontmatter template:

   ```markdown
   ---
   name: jarvis-<slug>
   description: <One sentence. Surfaces in the / palette.>
   disable-model-invocation: true
   ---
   ```

3. In the body, write clear **imperative** instructions for the agent.  Assume
   the agent is reading it cold.
4. If the skill wraps a shell script, reference it by path:
   ```markdown
   Run verbatim:
   ```bash
   python3 scripts/<script>.py <args>
   ```
5. State explicitly what the agent must NOT do (e.g. "do not implement inline").
6. Update `AGENTS.md` "Keeping docs current" row if you add a skill that
   changes observable behavior.

---

## `disable-model-invocation: true`

This flag tells Cursor to inject the SKILL.md content directly into the turn
**instead of** letting the model decide whether to use the skill.  It is
**required** for all `/jarvis-*` action skills so that the agent can't skip or
misinterpret the invocation.

---

## What skills are NOT for

- Replacing always-on `.mdc` rules (use `.cursor/rules/*.mdc` for persistent
  agent guidance).
- Auto-detecting user intent (no heuristics — explicit `/jarvis-<x>` only).
- One-off automation that belongs in `scripts/` (put the logic in a script;
  the skill is just the invocation wrapper).

---

## Conformance

`verify_lifecycle.py` assertion **A18** checks that `jarvis-new-task` is
correctly wired.  When you add a new skill that warrants its own assertion,
add it to `scripts/verify_lifecycle.py` and mirror it in
`scripts/test_verify_lifecycle.py`.

---

## See also

- `.cursor/skills/jarvis-new-task/SKILL.md` — reference implementation
- `.cursor/rules/*.mdc` — always-on agent guidance (distinct from skills)
- `docs/contributing/rules.md` — when to use `.mdc` vs `.md`
