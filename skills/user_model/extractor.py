#!/usr/bin/env python3
"""Heuristic signal extraction from user turns.

Per `jarvis.md` user-model capture protocol (Fork 1A): only turns containing
explicit signal phrases get surfaced for capture. The extractor is INTENTIONALLY
conservative — false positives create noise in the preferences file, false
negatives are recoverable (user can manually trigger 'remember this:').

Output of `detect_signals(text)` is a list of candidates. Each candidate has:
    - phrase: the signal phrase that matched (e.g. "you should")
    - category: principle | style | correction | domain | explicit_capture
    - confidence: 0.0–1.0 — how strong the signal is (higher = surface first)
    - span: (start, end) char offsets of the matched phrase
    - hint: short label for the AI to use when proposing a draft to the user
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    phrase: str
    category: str
    confidence: float
    span: tuple[int, int]
    hint: str


# (regex, category, confidence, hint)
# Categories:
#   principle  — architectural / behavioral rules ("never X", "always Y")
#   style      — communication preferences ("I prefer", "I like terse answers")
#   correction — user is fixing a behavior ("that's wrong", "instead of")
#   domain     — facts about user's world ("I use X", "we operate Y")
#   explicit_capture — user explicitly asked to remember ("remember this")
SIGNAL_PATTERNS: list[tuple[re.Pattern, str, float, str]] = [
    (re.compile(r"\bremember\s+(this|that|the\s+following)\b", re.I),
     "explicit_capture", 1.00, "Explicit save"),
    (re.compile(r"\bdon'?t\s+\w+\s+(?:that|this)\s+again\b", re.I),
     "correction", 0.95, "Don't-repeat correction"),
    (re.compile(r"\b(that's|that is)\s+wrong\b", re.I),
     "correction", 0.95, "Direct correction"),
    (re.compile(r"\binstead\s+of\b", re.I),
     "correction", 0.75, "Instead-of preference"),
    (re.compile(r"\b(?:why don'?t|why aren'?t|why\s+isn'?t|why\s+can'?t)\s+you\b", re.I),
     "correction", 0.80, "Implicit correction"),
    (re.compile(r"\bcan'?t\s+you\b", re.I),
     "correction", 0.70, "Capability nudge"),
    (re.compile(r"\bnext\s+time\b", re.I),
     "principle", 0.85, "Next-time principle"),
    (re.compile(r"\byou\s+should\b", re.I),
     "principle", 0.70, "Should-do principle"),
    (re.compile(r"\b(in\s+general|generally)\b", re.I),
     "principle", 0.60, "General principle"),
    (re.compile(r"\balways\b", re.I),
     "principle", 0.55, "Always-rule"),
    (re.compile(r"\bnever\b", re.I),
     "principle", 0.55, "Never-rule"),
    (re.compile(r"\bmake\s+sure\b", re.I),
     "principle", 0.60, "Make-sure rule"),
    (re.compile(r"\bI\s+(prefer|want|like)\b", re.I),
     "style", 0.65, "Stated preference"),
    (re.compile(r"\bI\s+don'?t\s+(like|want)\b", re.I),
     "style", 0.75, "Stated dislike"),
    (re.compile(r"\bI\s+(use|own|operate|run)\b", re.I),
     "domain", 0.55, "Domain fact"),
    (re.compile(r"\b(?:my|our)\s+(business|company|shop|store|workspace)\b", re.I),
     "domain", 0.50, "Domain fact"),
]


# Categories pre-confirmed safe to capture without inline confirm
# (per Fork 2A we surface ALL signals, but high-confidence ones can be
# annotated with auto-suggest in the UI.)
ALWAYS_CONFIRM_INLINE = True


def detect_signals(text: str) -> list[Signal]:
    """Scan a user turn for capture-worthy signal phrases.

    Returns a list of Signal objects (possibly empty). Order: highest
    confidence first; ties broken by earliest position. Duplicates of the
    same (category, span) are filtered.
    """
    if not text:
        return []

    found: list[Signal] = []
    seen: set[tuple[str, int, int]] = set()
    for pattern, category, confidence, hint in SIGNAL_PATTERNS:
        for match in pattern.finditer(text):
            key = (category, match.start(), match.end())
            if key in seen:
                continue
            seen.add(key)
            found.append(Signal(
                phrase=match.group(0),
                category=category,
                confidence=confidence,
                span=(match.start(), match.end()),
                hint=hint,
            ))

    found.sort(key=lambda s: (-s.confidence, s.span[0]))
    return found


def signal_summary(signals: list[Signal]) -> str:
    """Render a one-line summary suitable for AI logs / debug output."""
    if not signals:
        return "no signals"
    by_cat: dict[str, int] = {}
    for s in signals:
        by_cat[s.category] = by_cat.get(s.category, 0) + 1
    parts = [f"{n}×{c}" for c, n in sorted(by_cat.items())]
    return f"{len(signals)} signals: " + ", ".join(parts)


if __name__ == "__main__":
    import sys
    text = sys.stdin.read() if not sys.argv[1:] else " ".join(sys.argv[1:])
    sigs = detect_signals(text)
    print(signal_summary(sigs))
    for s in sigs:
        excerpt = text[max(0, s.span[0] - 20):min(len(text), s.span[1] + 20)]
        print(f"  [{s.category:18s}] conf={s.confidence:.2f} {s.hint:30s} …{excerpt}…")
