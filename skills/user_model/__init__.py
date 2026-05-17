"""skills/user_model — predictive model of how the user thinks.

Captures preference signals from user turns, persists structured preferences
to .cursor/rules/user-preferences.md (auto-loaded), and keeps an append-only
raw corpus for later distillation.

Public API:
    from skills.user_model import extractor, store

    # Detect signals in a user turn (heuristic phrase matching)
    candidates = extractor.detect_signals(user_text)

    # Append a raw user turn to the corpus (always, every turn)
    store.append_to_corpus(user_text, agent="bhaga", source="cursor")

    # Persist a confirmed preference to the auto-loaded preferences file
    store.add_preference(category="principle", text="X", source="2026-04-19 chat")

    # Read structured preferences back out
    store.list_preferences(category="style")
"""

from . import extractor, store

__all__ = ["extractor", "store"]
