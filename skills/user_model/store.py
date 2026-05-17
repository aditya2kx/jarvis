#!/usr/bin/env python3
"""Persistence layer for the user_model skill.

Two storage layers (Fork 3A — single auto-loaded preferences file):

1. Raw corpus (gitignored)
   ─ skills/user_model/data/corpus.jsonl
   ─ Append-only log of every user turn the agent processes
   ─ Cheap (no analysis), used for later distillation if desired
   ─ Lines: {"ts": ISO, "agent": str|null, "source": str, "text": str, "metadata": {...}}

2. Structured preferences (gittracked, auto-loads via Cursor rules)
   ─ .cursor/rules/user-preferences.md
   ─ Markdown with 4 fixed sections (Communication style, Design principles,
     Domain context, Decision history). Each section is a table.
   ─ This is what the AI reads at session start to predict user preferences.
   ─ Idempotent: add_preference() dedups by (category, normalized text).
   ─ NEVER duplicates content from `.cursor/rules/jarvis.md` Hard Lessons —
     instead links via the `Source` column (Fork 5: single source of truth).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from core.config_loader import project_dir


_PROJECT = pathlib.Path(project_dir())
CORPUS_PATH = _PROJECT / "skills" / "user_model" / "data" / "corpus.jsonl"
PREFERENCES_PATH = _PROJECT / ".cursor" / "rules" / "user-preferences.md"


# ── Categories → section headings in the preferences file ──────────
SECTION_TITLES = {
    "style":      "Communication style",
    "principle":  "Design principles",
    "domain":     "Domain context",
    "decision":   "Decision history",
}
SECTION_ORDER = ["style", "principle", "domain", "decision"]

# Headers per section. The first column is intentionally a stable serial id
# so links from PROGRESS.md / Hard Lessons can reference specific rows.
SECTION_HEADERS = {
    "style":     ["#", "Pattern", "Source"],
    "principle": ["#", "Principle", "Source"],
    "domain":    ["Topic", "Detail", "Source"],
    "decision":  ["Date", "Decision", "Picked", "Why"],
}

# Map intermediate category names from the extractor to file sections.
# 'correction' and 'explicit_capture' don't have their own section — they
# usually become principles or style entries depending on content. The AI
# is responsible for picking the right target section when proposing.
EXTRACTOR_TO_SECTION = {
    "style": "style",
    "principle": "principle",
    "domain": "domain",
    "correction": "principle",       # default: corrections become "next time, do X" principles
    "explicit_capture": "principle", # default: ask AI to override if user meant a different category
}


# ── Corpus ────────────────────────────────────────────────────────


def append_to_corpus(text, *, agent=None, source="cursor", metadata=None):
    """Append one user turn to the gitignored corpus jsonl."""
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "agent": agent,
        "source": source,
        "text": text,
        "metadata": metadata or {},
    }
    with CORPUS_PATH.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_corpus(*, since_ts=None, agent=None, limit=None):
    """Read corpus entries, optionally filtered by ts / agent."""
    if not CORPUS_PATH.exists():
        return []
    out = []
    with CORPUS_PATH.open() as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts and e.get("ts", "") < since_ts:
                continue
            if agent and e.get("agent") != agent:
                continue
            out.append(e)
            if limit and len(out) >= limit:
                break
    return out


# ── Preferences file ──────────────────────────────────────────────


@dataclass
class Preference:
    category: str
    fields: dict


def _resolve_section(category):
    """Map a category (extractor or section name) to a section key."""
    if category in SECTION_ORDER:
        return category
    return EXTRACTOR_TO_SECTION.get(category, "principle")


def _normalize(s):
    """For dedup comparisons — collapse whitespace, lowercase, strip punctuation."""
    return re.sub(r"\s+", " ", s.strip().lower().rstrip(".,;:"))


def list_preferences(category=None):
    """Read structured preferences out of the markdown file."""
    if not PREFERENCES_PATH.exists():
        return []

    raw = PREFERENCES_PATH.read_text()
    entries = []
    current_section = None
    headers = None

    for line in raw.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            title = m.group(1).strip()
            current_section = next(
                (k for k, v in SECTION_TITLES.items() if v == title), None
            )
            headers = None
            continue
        if not current_section:
            continue
        if line.strip().startswith("|") and "---" not in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if headers is None:
                headers = cells
                continue
            if not any(cells):
                continue
            entries.append(Preference(
                category=current_section,
                fields=dict(zip(headers, cells)),
            ))

    if category:
        target = _resolve_section(category)
        entries = [e for e in entries if e.category == target]
    return entries


def add_preference(category, fields, *, dedupe_key=None):
    """Append (or overwrite if duplicate) a preference into the markdown file.

    Args:
        category: 'style' | 'principle' | 'domain' | 'decision' (or extractor name)
        fields: dict matching SECTION_HEADERS[section]. Missing keys default to "".
        dedupe_key: column name to use for dedup (defaults to the second column,
                    which is usually the human-readable text). If a preference
                    with the same normalized value is already present, this
                    call is a no-op. Pass dedupe_key=None to disable dedup.

    Returns:
        ('added' | 'duplicate', new_or_existing_row)
    """
    section = _resolve_section(category)
    headers = SECTION_HEADERS[section]
    row_fields = {h: str(fields.get(h, "")).strip() for h in headers}

    existing = list_preferences(category=section)

    if dedupe_key is None:
        # default: dedup on the "main content" column (second one)
        dedupe_key = headers[1]
    if dedupe_key:
        target_norm = _normalize(row_fields.get(dedupe_key, ""))
        for e in existing:
            if _normalize(e.fields.get(dedupe_key, "")) == target_norm:
                return ("duplicate", e)

    # Re-render the whole file with the new row appended in the right section.
    PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    sections = _read_sections()
    sections.setdefault(section, []).append(row_fields)
    _write_sections(sections)
    return ("added", Preference(category=section, fields=row_fields))


def _read_sections():
    """Parse the file into {section_key: [row_dict, ...]}."""
    sections = {k: [] for k in SECTION_ORDER}
    if not PREFERENCES_PATH.exists():
        return sections

    for entry in list_preferences():
        sections.setdefault(entry.category, []).append(entry.fields)
    return sections


def _write_sections(sections):
    """Render the full preferences file from a {section: [rows]} map."""
    lines = [
        "---",
        "description: User preferences and accumulated context (auto-loaded)",
        "alwaysApply: true",
        "---",
        "",
        "# User Preferences",
        "",
        "Predictive model of Aditya's communication style, design principles, "
        "domain context, and prior decisions. Auto-loaded by Cursor at the start "
        "of every chat.",
        "",
        "**Single source of truth (Fork 5):** This file does NOT restate Hard "
        "Lessons (`.cursor/rules/jarvis.md` § Hard Lessons). When a preference "
        "is already a Hard Lesson, the `Source` column links to it. When a "
        "preference is NEW signal not yet codified elsewhere, it lives here.",
        "",
        "Maintained by `skills/user_model/`. Source corpus (gitignored) at "
        "`skills/user_model/data/corpus.jsonl`.",
        "",
    ]

    for key in SECTION_ORDER:
        rows = sections.get(key, [])
        title = SECTION_TITLES[key]
        headers = SECTION_HEADERS[key]
        lines.append(f"## {title}")
        lines.append("")
        if not rows:
            lines.append(f"_(none captured yet)_")
            lines.append("")
            continue
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join("---" for _ in headers) + "|")
        for row in rows:
            lines.append(
                "| " + " | ".join(str(row.get(h, "")).replace("|", "\\|") for h in headers) + " |"
            )
        lines.append("")

    PREFERENCES_PATH.write_text("\n".join(lines))


# ── CLI ───────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="user_model store CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Add a preference row")
    p_add.add_argument("--category", required=True,
                       choices=SECTION_ORDER + list(EXTRACTOR_TO_SECTION.keys()))
    p_add.add_argument("--fields-json", required=True,
                       help='JSON object of column→value, e.g. \'{"#": "1", "Pattern": "X"}\'')

    p_list = sub.add_parser("list", help="List preferences")
    p_list.add_argument("--category", default=None,
                        choices=SECTION_ORDER)

    p_corpus = sub.add_parser("corpus-tail", help="Show recent corpus entries")
    p_corpus.add_argument("--limit", type=int, default=10)

    args = parser.parse_args()

    if args.cmd == "add":
        fields = json.loads(args.fields_json)
        status, pref = add_preference(args.category, fields)
        print(json.dumps({"status": status, "section": pref.category, "fields": pref.fields},
                         indent=2))
    elif args.cmd == "list":
        for p in list_preferences(category=args.category):
            print(f"[{p.category}] {p.fields}")
    elif args.cmd == "corpus-tail":
        for e in read_corpus()[-args.limit:]:
            print(json.dumps(e, ensure_ascii=False))
    else:
        parser.print_help()
