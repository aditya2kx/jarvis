# Cursor rule authoring guide

## File extension: `.mdc` vs `.md`

| Location | Required extension | Why |
|---|---|---|
| `.cursor/rules/` (project rules) | **`.mdc`** | Only `.mdc` files are loaded by Cursor as project rules. `.md` files in this directory are silently ignored — frontmatter (`alwaysApply`, `globs`, `description`) is never parsed. |
| `docs/`, `agents/`, `skills/`, repo root (regular docs) | `.md` | Normal markdown documents; not loaded as Cursor rules. |
| `.cursor/rules/AGENTS.md` (special) | n/a | `AGENTS.md` at the workspace root is loaded by Cursor by filename, regardless of extension, as the project context file. |

**The `.md`-vs-`.mdc` distinction is enforced mechanically:** `verify_lifecycle.py` assertion A15 fails CI if any `.md` file appears in `.cursor/rules/`. Add new rules as `.mdc` or A15 will catch it.

## Load modes

Every `.mdc` rule file has YAML frontmatter that controls when Cursor loads it:

```yaml
---
description: One-line summary shown in the rules picker
alwaysApply: true     # loaded into every agent context
---
```

```yaml
---
description: BHAGA agent card
globs:
  - "agents/bhaga/**"
alwaysApply: false    # loaded only when editing matching files
---
```

```yaml
---
description: On-demand reference
globs: []             # never auto-loaded; must be @-mentioned explicitly
alwaysApply: false
---
```

| Load mode | Use for |
|---|---|
| `alwaysApply: true` | Spine rules that every agent must always obey: behavioral anchor, self-drive, new-requirement intake, preference consult, user preferences, routing card, plan-readiness checklist |
| `globs: ["agents/foo/**"]` | Agent cards and domain rules that are only relevant when working in a specific subtree |
| `globs: []` | Long reference docs (playbooks, workflow templates) that are too large to always load but useful on demand |

## Assertion A16 guards load semantics

`verify_lifecycle.py` assertion A16 checks that known always-on rules stayed `alwaysApply: true` and that `jarvis-hard-lessons.mdc` (intentionally on-demand) stayed glob-scoped. If you change a rule's load mode, make sure A16's fixture list is updated accordingly.

## Adding a new rule

1. Create `.cursor/rules/<name>.mdc` (not `.md`).
2. Set the frontmatter `description`, `alwaysApply`, and `globs` per the table above.
3. For a new always-on rule: add the filename to `ALWAYS_ON` in `verify_lifecycle.py::assert_16_rule_semantics_preserved` and add a row to the Tier 1 table in `AGENTS.md`.
4. Run `python3 scripts/verify_lifecycle.py --assert 15 16` — both must PASS before pushing.
