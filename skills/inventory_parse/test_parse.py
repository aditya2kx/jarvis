"""Tests for skills/inventory_parse/parse.py.

Covers the parse_qty semantics table from akshaya.mdc plus the multi-part
extension, the Blade legacy entry, and null sentinel handling.
"""

import pytest
from skills.inventory_parse.parse import parse_qty, FIELD_REGISTRY, ACTIVE_BASES


# ---------------------------------------------------------------------------
# parse_qty — canonical cases from akshaya.mdc
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # Whole + percentage
    ("23+80%",          23.80),
    ("33+30%",          33.30),
    ("16+85%",          16.85),
    ("17+40%",          17.40),
    ("12+75%",          12.75),
    ("4+90%",           4.90),
    ("3+80%",           3.80),
    ("5+80%",           5.80),
    ("19+60%",          19.60),
    # Shift-typo '^' instead of '%'
    ("15+98^",          15.98),
    # Comma-separated (second value is pct)
    ("3 boxes, 75% cambro",  3.75),
    ("3 cases",              3.0),
    ("9 boxes",              9.0),
    # Multi-part additive
    ("3 + 1 bag + 70%", 4.70),
    # Multiple fractional parts
    ("16+85%+15%",      17.00),
    # Standalone percentage
    ("90%",             0.90),
    ("60%",             0.60),
    ("55%",             0.55),
    ("50%",             0.50),
    # Whole number only
    ("17",              17.0),
    ("5",               5.0),
    # Decimal
    ("3.75",            3.75),
    # Extra whitespace
    ("  12+75%  ",      12.75),
    # Leading-dot decimals (staff entry for near-empty tub, e.g. ".95" means 0.95)
    (".95",             0.95),
    (".5",              0.50),
    (".85",             0.85),
    (".5 cambro",       0.50),
])
def test_parse_qty_happy(raw, expected):
    result = parse_qty(raw)
    assert result is not None
    assert abs(result - expected) < 1e-9, f"parse_qty({raw!r}) = {result}, expected {expected}"


@pytest.mark.parametrize("raw", [
    "N/A", "n/a", "na", "NA", "-", "o", "", None,
])
def test_parse_qty_null_sentinels(raw):
    assert parse_qty(raw) is None, f"Expected None for {raw!r}"


def test_parse_qty_no_number_returns_none():
    # A string with no digits but not a sentinel -> no number found -> None
    result = parse_qty("abc")
    assert result is None


# ---------------------------------------------------------------------------
# FIELD_REGISTRY
# ---------------------------------------------------------------------------

def test_active_bases_in_registry():
    for base in ACTIVE_BASES:
        assert base in FIELD_REGISTRY, f"{base} missing from FIELD_REGISTRY"
        cat, unit = FIELD_REGISTRY[base]
        assert cat == "base"
        assert unit == "tubs"


def test_blade_in_registry_for_backfill():
    assert "Blade" in FIELD_REGISTRY
    cat, unit = FIELD_REGISTRY["Blade"]
    assert cat == "base"


def test_active_bases_count():
    assert len(ACTIVE_BASES) == 8


# ---------------------------------------------------------------------------
# Scalability: adding a new entry to FIELD_REGISTRY requires no other change
# ---------------------------------------------------------------------------

def test_field_registry_is_dict():
    assert isinstance(FIELD_REGISTRY, dict)
    # All values are 2-tuples (category, unit)
    for name, (cat, unit) in FIELD_REGISTRY.items():
        assert isinstance(cat, str) and cat, f"{name}: category must be non-empty str"
        assert isinstance(unit, str) and unit, f"{name}: unit must be non-empty str"
