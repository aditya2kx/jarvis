"""Inventory quantity parser for Palmetto closing-form free-text values.

Parses ClickUp closing-form entries like '33+30%', '16+85%+15%', '15+98^'
into a canonical float quantity (tubs for bases).

Semantics (from akshaya.mdc data parsing table):
    '23+80%'               -> 23.80   (whole units + partial percentage)
    '15+98^'               -> 15.98   (^ is a shift-typo for %)
    '3 boxes, 75% cambro'  -> 3.75    (comma-separated, second value is pct)
    '3 + 1 bag + 70%'      -> 4.70    (multi-part additive; bag ≈ box, 1:1)
    '16+85%+15%'           -> 17.00   (multiple fractional parts)
    'N/A', '-', 'o', ''    -> None    (missing; do NOT coerce to 0)
    '90%'                  -> 0.90    (standalone percentage = fraction of a unit)

Convention: the first token uses its own % sign (or lack thereof); every
subsequent token after a '+' or ',' is treated as a percentage regardless of
whether a '%' or '^' is present.  This matches `parse_inv()` in
agents/akshaya/scripts/forecast_v2.py lines 121-147.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Field registry: ClickUp field display name -> (category, canonical unit)
# Scalability: adding a new category = add entries here, no schema migration.
# The ingest script drives off this dict so new fields are picked up
# automatically.
# ---------------------------------------------------------------------------

FIELD_REGISTRY: dict[str, tuple[str, str]] = {
    # HQ bases tracked in Grafana Order Assistant section (slice A)
    "Açaí":     ("base", "tubs"),
    "Coconut":  ("base", "tubs"),
    "Tropical": ("base", "tubs"),
    "Mango":    ("base", "tubs"),
    "Pitaya":   ("base", "tubs"),
    "Matcha":   ("base", "tubs"),
    "Ube":      ("base", "tubs"),
    "Pog":      ("base", "tubs"),
    # Legacy base kept for historical backfill (removed from active allocation
    # per akshaya.mdc but still appears in old form submissions)
    "Blade":    ("base", "tubs"),
}

# Convenience: just the active bases (excluding legacy Blade) for Grafana
ACTIVE_BASES: tuple[str, ...] = (
    "Açaí", "Coconut", "Tropical", "Mango", "Pitaya", "Matcha", "Ube", "Pog"
)

# Missing / null sentinels
_NULL_SENTINELS = frozenset({"n/a", "na", "-", "o", ""})


def parse_qty(raw: str | None) -> float | None:
    """Parse a closing-form free-text inventory quantity into a float.

    Args:
        raw: Raw string from the ClickUp custom field, e.g. '33+30%'.

    Returns:
        Parsed float quantity, or None if the value is missing/null.
        A standalone '90%' returns 0.90 (fraction of one unit).

    This function is a clean re-implementation of parse_inv() from
    agents/akshaya/scripts/forecast_v2.py, with these additions:
    - Handles multi-part inputs like '16+85%+15%' (sums all fractions).
    - Explicit None return for null sentinels (distinguishes missing from zero).
    - No side-effects: pure function, safe to call in parallel.
    """
    if raw is None or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.lower() in _NULL_SENTINELS:
        return None

    # Split on '+' or ','
    parts = re.split(r"[+,]", raw)
    total = 0.0
    found = False

    # Unit-word tokens (bag, box, cambro, …) are WHOLE-UNIT tokens even when
    # they appear after a '+'.  akshaya.mdc: "bag ≈ box (1:1). Multi-part additive."
    _UNIT_WORDS = re.compile(
        r"\b(?:bag|box|boxes|bags|cambro|jug|container|bucket|stack|stacks)\b",
        re.IGNORECASE,
    )

    for idx, part in enumerate(parts):
        nums = re.findall(r"(\d+\.?\d*)", part)
        if not nums:
            continue
        val = float(nums[0])
        has_pct = "%" in part or "^" in part
        has_unit_word = bool(_UNIT_WORDS.search(part))
        if idx == 0:
            # First token: respect its own '%' (or '^' shift-typo)
            total += val / 100.0 if has_pct else val
        elif has_unit_word and not has_pct:
            # Subsequent token with a unit word but NO % → whole units (e.g. '1 bag')
            total += val
        else:
            # Subsequent token without a unit word, or with %, → percentage (e.g. '70%', '75% cambro')
            total += val / 100.0
        found = True

    return total if found else None
