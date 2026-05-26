"""Tests for daily_diff.py — diff computation and message formatting."""

from __future__ import annotations

import pytest

from daily_diff import (
    _normalize_value,
    compute_tab_diff,
    format_diff_message,
)


# ---------------------------------------------------------------------------
# Fixtures: sample row data
# ---------------------------------------------------------------------------

def _make_row(date: str, employee: str, hours: str, tips: str) -> dict[str, str]:
    return {"date": date, "employee_name": employee, "hours": hours, "tips": tips}


SAMPLE_DATE = "2026-05-25"


@pytest.fixture
def matching_rows():
    """Prod and staging have identical data."""
    rows = [
        _make_row(SAMPLE_DATE, "Garcia, Jacob", "8.0", "42.50"),
        _make_row(SAMPLE_DATE, "Alvarez, Sebastian", "6.5", "38.00"),
    ]
    return rows, list(rows)


@pytest.fixture
def differing_rows():
    """Prod and staging differ in one cell (tips for Garcia)."""
    prod = [
        _make_row(SAMPLE_DATE, "Garcia, Jacob", "8.0", "42.50"),
        _make_row(SAMPLE_DATE, "Alvarez, Sebastian", "6.5", "38.00"),
    ]
    staging = [
        _make_row(SAMPLE_DATE, "Garcia, Jacob", "8.0", "45.00"),
        _make_row(SAMPLE_DATE, "Alvarez, Sebastian", "6.5", "38.00"),
    ]
    return prod, staging


@pytest.fixture
def missing_row_staging():
    """Prod has a row that staging is missing."""
    prod = [
        _make_row(SAMPLE_DATE, "Garcia, Jacob", "8.0", "42.50"),
        _make_row(SAMPLE_DATE, "Alvarez, Sebastian", "6.5", "38.00"),
    ]
    staging = [
        _make_row(SAMPLE_DATE, "Garcia, Jacob", "8.0", "42.50"),
    ]
    return prod, staging


@pytest.fixture
def missing_row_prod():
    """Staging has an extra row not in prod."""
    prod = [
        _make_row(SAMPLE_DATE, "Garcia, Jacob", "8.0", "42.50"),
    ]
    staging = [
        _make_row(SAMPLE_DATE, "Garcia, Jacob", "8.0", "42.50"),
        _make_row(SAMPLE_DATE, "Alvarez, Sebastian", "6.5", "38.00"),
    ]
    return prod, staging


# ---------------------------------------------------------------------------
# compute_tab_diff tests
# ---------------------------------------------------------------------------

class TestComputeTabDiff:
    def test_matching_rows_no_diffs(self, matching_rows):
        prod, staging = matching_rows
        result = compute_tab_diff("daily", prod, staging, SAMPLE_DATE)
        assert result["tab"] == "daily"
        assert result["prod_rows"] == 2
        assert result["staging_rows"] == 2
        assert result["cell_diffs"] == []

    def test_differing_cell_detected(self, differing_rows):
        prod, staging = differing_rows
        result = compute_tab_diff("daily", prod, staging, SAMPLE_DATE)
        assert result["prod_rows"] == 2
        assert result["staging_rows"] == 2
        diffs = result["cell_diffs"]
        assert len(diffs) == 1
        assert diffs[0]["column"] == "tips"
        assert diffs[0]["prod"] == "42.50"
        assert diffs[0]["staging"] == "45.00"

    def test_missing_row_in_staging(self, missing_row_staging):
        prod, staging = missing_row_staging
        result = compute_tab_diff("daily", prod, staging, SAMPLE_DATE)
        assert result["prod_rows"] == 2
        assert result["staging_rows"] == 1
        diffs = result["cell_diffs"]
        assert any(d["staging"] == "MISSING" for d in diffs)

    def test_extra_row_in_staging(self, missing_row_prod):
        prod, staging = missing_row_prod
        result = compute_tab_diff("daily", prod, staging, SAMPLE_DATE)
        assert result["prod_rows"] == 1
        assert result["staging_rows"] == 2
        diffs = result["cell_diffs"]
        assert any(d["prod"] == "MISSING" for d in diffs)

    def test_empty_tabs(self):
        result = compute_tab_diff("daily", [], [], SAMPLE_DATE)
        assert result["prod_rows"] == 0
        assert result["staging_rows"] == 0
        assert result["cell_diffs"] == []

    def test_zero_rows_for_target_date(self):
        prod = [_make_row("2026-05-20", "Garcia, Jacob", "8.0", "42.50")]
        staging = [_make_row("2026-05-20", "Garcia, Jacob", "8.0", "42.50")]
        result = compute_tab_diff("daily", prod, staging, SAMPLE_DATE)
        assert result["prod_rows"] == 0
        assert result["staging_rows"] == 0
        assert result["cell_diffs"] == []

    def test_period_tab_uses_period_end_key(self):
        prod = [{"period_end": SAMPLE_DATE, "employee_name": "A", "bonus": "100"}]
        staging = [{"period_end": SAMPLE_DATE, "employee_name": "A", "bonus": "200"}]
        result = compute_tab_diff("review_bonus_period", prod, staging, SAMPLE_DATE)
        assert result["prod_rows"] == 1
        assert result["staging_rows"] == 1
        assert len(result["cell_diffs"]) == 1
        assert result["cell_diffs"][0]["column"] == "bonus"


# ---------------------------------------------------------------------------
# normalize_value tests
# ---------------------------------------------------------------------------

class TestNormalizeValue:
    def test_numeric_normalization(self):
        assert _normalize_value("42.50") == _normalize_value("42.5")
        assert _normalize_value("42.50") == _normalize_value("  42.5  ")

    def test_string_passthrough(self):
        assert _normalize_value("Garcia, Jacob") == "Garcia, Jacob"

    def test_empty_string(self):
        assert _normalize_value("") == ""

    def test_whitespace_stripped(self):
        assert _normalize_value("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# format_diff_message tests
# ---------------------------------------------------------------------------

class TestFormatDiffMessage:
    def test_no_diffs_shows_checkmark(self):
        results = [
            {"tab": "daily", "prod_rows": 1, "staging_rows": 1, "cell_diffs": []},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert ":white_check_mark:" in msg
        assert "matches" in msg.lower()
        assert SAMPLE_DATE in msg

    def test_diffs_shows_warning(self, differing_rows):
        prod, staging = differing_rows
        result = compute_tab_diff("daily", prod, staging, SAMPLE_DATE)
        msg = format_diff_message(SAMPLE_DATE, [result])
        assert ":warning:" in msg
        assert "1 diff" in msg
        assert "tips" in msg

    def test_multiple_tabs(self):
        results = [
            {"tab": "daily", "prod_rows": 5, "staging_rows": 5, "cell_diffs": []},
            {"tab": "labor_daily", "prod_rows": 3, "staging_rows": 3, "cell_diffs": [
                {"tab": "labor_daily", "row_key": "k1", "column": "hours", "prod": "8", "staging": "7"},
            ]},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert "daily" in msg
        assert "labor_daily" in msg
        assert "1 diff" in msg

    def test_all_empty_tabs(self):
        results = [
            {"tab": "daily", "prod_rows": 0, "staging_rows": 0, "cell_diffs": []},
            {"tab": "labor_daily", "prod_rows": 0, "staging_rows": 0, "cell_diffs": []},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert ":white_check_mark:" in msg

    def test_truncation_at_five_diffs(self):
        many_diffs = [
            {"tab": "daily", "row_key": f"k{i}", "column": f"c{i}", "prod": "a", "staging": "b"}
            for i in range(10)
        ]
        results = [
            {"tab": "daily", "prod_rows": 10, "staging_rows": 10, "cell_diffs": many_diffs},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert "5 more" in msg
