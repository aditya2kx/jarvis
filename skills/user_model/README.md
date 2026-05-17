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
| `extractor.py` | Heuristic signal detection. `detect_signals(text)` returns a list of `Signal(phrase, category, confidence, span, hint)` — categories: principle, style, correction, domain, explicit_capture. |
| `store.py` | Persistence. `append_to_corpus()` for raw log. `add_preference(category, fields)` for the structured file (idempotent — dedups by main content column). `list_preferences()` for read-back. |
| `__init__.py` | Re-exports `extractor` + `store`. |

Deferred to v2 (intentionally not built today):
- `query.py` — programmatic relevance retrieval. Not needed yet because `.cursor/rules/user-preferences.md` auto-loads as context, so the AI naturally has all preferences available when reasoning. Add when the file gets too large to fit comfortably (~10KB+).
- `digest.py` — re-distill the corpus into the preferences file (auto extract → propose → confirm). Not needed yet because v1 corpus is small and confirmations happen inline. Add when there's enough corpus volume to justify periodic distillation.

## The capture protocol (AI-side)

Codified in `.cursor/rules/jarvis.md` § "During a Session". Every assistant turn:

1. **Append** the latest user message to `skills/user_model/data/corpus.jsonl` via `store.append_to_corpus()`. Cheap, always.
2. **Extract** signals via `extractor.detect_signals(user_text)`.
3. **If signals found**, surface inline at end of response:
   > *Noting under [Section]: 'rephrased one-liner'. Reply `y`, `n`, or rewrite text to refine.*
4. **On NEXT turn**, parse the user's first line for confirm/skip/edit tokens against any pending captures, then call `store.add_preference()` for confirmed ones.

## When to use the preferences file mid-decision

Before making any ambiguous architectural call:
- Re-read `.cursor/rules/user-preferences.md` (it's already in context — this is just a reminder to *consult* it, not to load it).
- Check the **Decision history** section for analogous prior decisions.
- Check the **Design principles** section for governing principles.
- If you find a relevant precedent, mirror it. If you don't, surface the decision per `dev-workflow-decisions.mdc`.

## Multi-agent reuse

The skill is global — every Jarvis agent (CHITRA, CHANAKYA, AKSHAYA, BHAGA, future ones) consumes the same preferences file. Captures are tagged with the originating agent in the corpus (`agent` field) so future analysis can answer questions like "is this preference universal or BHAGA-specific?"

## CLI

```bash
# Append a preference manually (e.g. when user explicitly says "remember this")
python -m skills.user_model.store add --category principle \
    --fields-json '{"#": "X", "Principle": "Single source of truth", "Source": "2026-04-19 chat"}'

# List all preferences
python -m skills.user_model.store list

# List by category
python -m skills.user_model.store list --category style

# Tail the raw corpus
python -m skills.user_model.store corpus-tail --limit 20
```
