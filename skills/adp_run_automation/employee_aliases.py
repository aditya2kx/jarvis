#!/usr/bin/env python3
"""Automatic detection + onboarding of newly-hired employees in ADP scrapes.

Problem: ADP exports use two forms of the same name — Timecard XLSX has
"Last First [MI]" (no comma), Earnings XLSX has "Last, First [MI]" (with
comma). The store profile (`palmetto.json`) maps every observed raw form
to a single canonical "Last, First" via `employees.aliases`. When a new
hire shows up in a scrape, their raw_name has no entry, so downstream the
record gets recorded with `employee_id == raw_name` — that splits one
person into two ledger identities (e.g. "Padron Lisette" AND "Padron,
Lisette"), breaks joins, and quietly forks the tip pool.

This module closes that loop AUTOMATICALLY (no human in the loop required):

    1. After parsing any ADP export, call `detect_new_employees(records, aliases)`
       to extract the set of raw_names not yet covered.
    2. For each new raw_name, derive the canonical "Last, First" form
       via `derive_canonical(raw)` (one-token-then-comma rule).
    3. `update_profile_with_new_aliases(profile_path, new_pairs)` rewrites
       the JSON in-place, adding BOTH forms ("X Y", "X, Y") -> "X, Y".
    4. Send a Slack DM via notify.py so the operator sees the new addition
       and can correct any mis-parsed canonical (e.g. compound last names).

The caller (typically backfill_from_downloads.py) then RE-PARSES the
source files with the updated aliases map so the records written to the
raw sheets get canonical employee_ids the first time, not raw forms.
"""

from __future__ import annotations

import json
import pathlib
from typing import Iterable


def derive_canonical(raw_name: str) -> str:
    """Convert a raw ADP name into the canonical "Last, First [middle/MI]" form.

    Rules (matches the convention in palmetto.json):
        * If the name already contains a comma, return it stripped.
        * Otherwise, the FIRST whitespace-separated token is the last name;
          the rest is the first name + any middle/MI. Insert a comma after
          the first token.

    Examples:
        "Padron Lisette"     -> "Padron, Lisette"
        "Latham Aubree N"    -> "Latham, Aubree N"
        "Garcia, Jacob"      -> "Garcia, Jacob"        (already canonical)
        "Johnson Dolce J"    -> "Johnson, Dolce J"

    Caveat: compound last names (e.g. "Van Der Berg Anna") will be
    mis-split as ("Van", "Der Berg Anna"). Operator should correct these
    via the Slack alert that fires whenever a new alias is auto-added.
    """
    name = raw_name.strip()
    if "," in name:
        # Normalize whitespace around the comma.
        last, _, rest = name.partition(",")
        return f"{last.strip()}, {rest.strip()}"
    parts = name.split()
    if len(parts) < 2:
        # Single-token name (e.g. mononym) — can't form Last,First. Keep as-is.
        return name
    last = parts[0]
    rest = " ".join(parts[1:])
    return f"{last}, {rest}"


def detect_new_employees(
    records: Iterable[dict],
    aliases: dict[str, str],
    *,
    raw_name_field: str = "raw_employee_name",
    fallback_field: str = "employee_name",
) -> list[tuple[str, str]]:
    """Return [(raw_name, derived_canonical), ...] for unknown employees.

    Looks up each record's raw_employee_name (or employee_name as fallback
    if raw is empty/missing) in `aliases`. Any value not present gets a
    derived canonical guess. Duplicates within the batch are de-duped.

    Empty/whitespace raw_names are skipped silently — they show up in
    summary rows like "Total Paid Hours:" headers that the parser couldn't
    associate with a person.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for rec in records:
        raw = (rec.get(raw_name_field) or rec.get(fallback_field) or "").strip()
        if not raw or raw in aliases or raw in seen:
            continue
        seen.add(raw)
        out.append((raw, derive_canonical(raw)))
    return out


def update_profile_with_new_aliases(
    profile_path: pathlib.Path,
    new_pairs: list[tuple[str, str]],
) -> dict:
    """Write new aliases into the store profile JSON. Returns updated profile dict.

    For each (raw, canonical) pair, adds both forms to aliases:
        raw       -> canonical    (the raw form we just observed)
        canonical -> canonical    (so the canonical form is also stable)

    Idempotent: skips pairs already present. Preserves existing JSON
    formatting via json.dump(indent=2) — should match the prettified style
    palmetto.json already uses.
    """
    profile = json.loads(profile_path.read_text())
    aliases = profile.setdefault("employees", {}).setdefault("aliases", {})
    added = 0
    for raw, canonical in new_pairs:
        if raw not in aliases:
            aliases[raw] = canonical
            added += 1
        if canonical not in aliases:
            aliases[canonical] = canonical
            added += 1
    if added:
        profile_path.write_text(json.dumps(profile, indent=2) + "\n")
    return profile
