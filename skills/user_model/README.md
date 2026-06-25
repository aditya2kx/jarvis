# skills/user_model

Predictive model of how Aditya thinks. Captures preference signals from user turns and persists them to a single auto-loaded preferences file (`.cursor/rules/user-preferences.md`) so any future Jarvis turn — any chat, any agent — has accumulated context to predict what the user will want.

## Why it exists

Two existing systems are *similar* but neither does what the user-model does:

- `~/.cursor/skills-cursor/skill-evolution/` — mandates persisting **corrections** after they happen. Reactive.
- `.cursor/rules/jarvis.md` § Hard Lessons — project-specific behavior rules. Imperative.

The user-model is **predictive** — capture preference signals proactively, so future ambiguous decisions can mirror the user's prior judgments instead of needing to ask. Per the user (2026-04-19): *"anytime I provide inputs or thoughts, it starts building a model to understand how I would have responded to use in other problems."*

## Architecture

| Layer | What | Where | Tracked? |
|---|---|---|---|
| **Raw corpus** | Append-only log of every user turn | `skills/user_model/data/corpus.jsonl` | gitignored |
| **Distilled preferences** | 4-section markdown (style / principles / domain / decisions) | `.cursor/rules/user-preferences.md` | **gittracked + auto-loads on every chat** |
| **Cross-references** | When a preference duplicates a Hard Lesson, the preferences file links to it instead of restating | `Source` column | — |

The two-layer split: corpus is rich + private + can be redistilled differently later; preferences file is curated + public + fed straight into Cursor's context window every session.

## Forks chosen (2026-04-19)

| Fork | Pick | Meaning |
|---|---|---|
| 1 — Capture aggressiveness | A | Heuristic-triggered (signal phrases) — not every turn, not manual-only |
| 2 — Confirmation | A | Inline confirm before persist — "Noting: 'X'. Reply y/n/edit" |
| 3 — Storage | A | Single `.cursor/rules/user-preferences.md` file (auto-loads) |
| 4 — Naming | A | Skill, no new agent |
| 5 — Hard Lessons relationship | (cross-reference) | Single source of truth — preferences file links, never duplicates |

## Module map

| Module | Purpose |
|---|---|
| `extractor.py` | Heuristic signal detection. `detect_signals(text)` returns a list of `Signal(phrase, category, confidence, span, hint)`. |
| `store.py` | Persistence. `append_to_corpus()` for raw log. `add_preference(category, fields)` — now **guardrail-gated** for `style`/`principle` rows. `list_preferences()` for read-back. |
| `guardrail.py` | **Generalizability guardrail.** `score_candidate(text) -> GuardrailResult` scores a candidate across 6 criteria (generalizable, not-prescriptive, scoped, non-duplicate, actionable, durable). Criterion 1 (generalizable) is a hard gate — task-specific tokens always score 0. `add_preference()` rejects below threshold (default 4/6). CLI: `python -m skills.user_model.guardrail score "<text>"`. |
| `backfill.py` | **One-shot idempotent backfill.** Extracts standing preferences from `bhaga-principles.md`, `CONTRIBUTING.md`, `jarvis.md` Hard Lessons, and the 5 Issue #70 jam answers; runs each through the guardrail; `add_preference`s passers. Run: `python -m skills.user_model.backfill`. |
| `__init__.py` | Re-exports `extractor` + `store`. |

Deferred to v2:
- `query.py` — programmatic relevance retrieval. Not needed yet; preferences auto-load.
- `digest.py` — re-distill the corpus into the preferences file. Add when corpus volume justifies it.

## The mechanical preference loop

```
User answer / turn
  → corpus.jsonl append (always — via store.append_to_corpus)
  → extractor.detect_signals
  → If signals: guardrail.score_candidate
      → PASS (>=4/6): propose to operator → on confirm: store.add_preference
           → guardrail re-checked at write; rejected if < threshold
      → FAIL: discard (task-specific / transient / non-actionable)
  → user-preferences.md auto-loads next chat
       → agent consults before any AskQuestion (.cursor/rules/preference-consult.md)
```

**Honest limit:** Cursor hooks do not fire for `AskQuestion` or user-turn text events (`beforeSubmitPrompt` also does not fire — empirically confirmed June 2026). The corpus append must be called explicitly by the agent. The guardrail is the mechanical quality gate regardless of how an entry arrives.

## The capture protocol (AI-side)

Codified in `.cursor/rules/preference-consult.md` (always-on). Every assistant turn:

1. **Before `AskQuestion`**: check `.cursor/rules/user-preferences.md` (already in context). If an ACTIVE preference answers it, apply it and do not ask.
2. **Append** the latest user message to `skills/user_model/data/corpus.jsonl` via `store.append_to_corpus()`. Cheap, always.
3. **Extract** signals via `extractor.detect_signals(user_text)`.
4. **If signals found + guardrail passes**, surface inline:
   > *Noting under [Section]: 'rephrased one-liner'. Reply `y`, `n`, or rewrite text to refine.*
5. **On confirm**, call `store.add_preference()`. The guardrail re-checks at write; items below threshold are rejected.

## When to use the preferences file mid-decision

Before any ambiguous architectural call:
- `.cursor/rules/user-preferences.md` is already in context — consult the **Design principles** and **Decision history** sections.
- If you find a relevant precedent, mirror it. If you don't, surface the decision per `dev-workflow-decisions.mdc`.

## Multi-agent reuse

The skill is global — every Jarvis agent (CHITRA, CHANAKYA, AKSHAYA, BHAGA, future ones) consumes the same preferences file. Captures are tagged with the originating agent in the corpus (`agent` field).

## CLI

```bash
# Quick lookup (recommended front door)
python3 scripts/prefs.py list                     # all preferences
python3 scripts/prefs.py list --category principle
python3 scripts/prefs.py search "diagnosis"
python3 scripts/prefs.py score "For BHAGA: always prefer sandbox over prod"

# Score a candidate (guardrail preview — no writes)
python -m skills.user_model.guardrail score "text here" [--threshold N]

# Backfill from principle docs (idempotent)
python -m skills.user_model.backfill [--dry-run] [--verbose]

# Add a preference manually (after explicit operator confirmation)
python -m skills.user_model.store add --category principle \
    --fields-json '{"#": "X", "Principle": "...", "Source": "..."}'

# Tail the raw corpus
python -m skills.user_model.store corpus-tail --limit 20
```
