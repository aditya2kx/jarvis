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
    return {"date_local": date, "employee_name": employee, "hours": hours, "tips": tips}


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
        result = compute_tab_diff("Model", "daily", prod, staging)
        assert result["sheet"] == "Model"
        assert result["tab"] == "daily"
        assert result["prod_rows"] == 2
        assert result["staging_rows"] == 2
        assert result["cell_diffs"] == []

    def test_differing_cell_detected(self, differing_rows):
        prod, staging = differing_rows
        result = compute_tab_diff("Model", "daily", prod, staging)
        assert result["prod_rows"] == 2
        assert result["staging_rows"] == 2
        diffs = result["cell_diffs"]
        assert len(diffs) == 1
        assert diffs[0]["column"] == "tips"
        assert diffs[0]["prod"] == "42.50"
        assert diffs[0]["staging"] == "45.00"

    def test_missing_row_in_staging(self, missing_row_staging):
        prod, staging = missing_row_staging
        result = compute_tab_diff("Model", "daily", prod, staging)
        assert result["prod_rows"] == 2
        assert result["staging_rows"] == 1
        diffs = result["cell_diffs"]
        assert any(d["staging"] == "MISSING" for d in diffs)

    def test_extra_row_in_staging(self, missing_row_prod):
        prod, staging = missing_row_prod
        result = compute_tab_diff("Model", "daily", prod, staging)
        assert result["prod_rows"] == 1
        assert result["staging_rows"] == 2
        diffs = result["cell_diffs"]
        assert any(d["prod"] == "MISSING" for d in diffs)

    def test_empty_tabs(self):
        result = compute_tab_diff("Model", "daily", [], [])
        assert result["prod_rows"] == 0
        assert result["staging_rows"] == 0
        assert result["cell_diffs"] == []

    def test_ignored_columns_excluded(self):
        """Rows differing only in scraped_at_utc should be considered equal."""
        prod = [{"date_local": SAMPLE_DATE, "employee_name": "A", "tips": "10", "scraped_at_utc": "2026-05-25T02:00:00Z"}]
        staging = [{"date_local": SAMPLE_DATE, "employee_name": "A", "tips": "10", "scraped_at_utc": "2026-05-25T04:30:00Z"}]
        result = compute_tab_diff("ADP Raw", "shifts", prod, staging)
        assert result["cell_diffs"] == []

    def test_cross_sheet_label(self, differing_rows):
        prod, staging = differing_rows
        result = compute_tab_diff("Square Raw", "transactions", prod, staging)
        assert result["sheet"] == "Square Raw"
        assert result["tab"] == "transactions"
        assert len(result["cell_diffs"]) == 1
        assert result["cell_diffs"][0]["sheet"] == "Square Raw"


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
            {"sheet": "Model", "tab": "daily", "prod_rows": 1, "staging_rows": 1, "cell_diffs": []},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert ":white_check_mark:" in msg
        assert "matches" in msg.lower()
        assert SAMPLE_DATE in msg

    def test_diffs_shows_warning(self, differing_rows):
        prod, staging = differing_rows
        result = compute_tab_diff("Model", "daily", prod, staging)
        msg = format_diff_message(SAMPLE_DATE, [result])
        assert ":warning:" in msg
        assert "1 diff" in msg
        assert "tips" in msg

    def test_multiple_sheets_grouped(self):
        results = [
            {"sheet": "Model", "tab": "daily", "prod_rows": 5, "staging_rows": 5, "cell_diffs": []},
            {"sheet": "Model", "tab": "labor_daily", "prod_rows": 3, "staging_rows": 3, "cell_diffs": [
                {"sheet": "Model", "tab": "labor_daily", "row_key": "k1", "column": "hours", "prod": "8", "staging": "7"},
            ]},
            {"sheet": "ADP Raw", "tab": "shifts", "prod_rows": 10, "staging_rows": 10, "cell_diffs": []},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert "Model" in msg
        assert "ADP Raw" in msg
        assert "1 diff" in msg

    def test_all_empty_tabs(self):
        results = [
            {"sheet": "Model", "tab": "daily", "prod_rows": 0, "staging_rows": 0, "cell_diffs": []},
            {"sheet": "ADP Raw", "tab": "shifts", "prod_rows": 0, "staging_rows": 0, "cell_diffs": []},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert ":white_check_mark:" in msg

    def test_truncation_at_eight_diffs(self):
        many_diffs = [
            {"sheet": "Model", "tab": "daily", "row_key": f"k{i}", "column": f"c{i}", "prod": "a", "staging": "b"}
            for i in range(12)
        ]
        results = [
            {"sheet": "Model", "tab": "daily", "prod_rows": 12, "staging_rows": 12, "cell_diffs": many_diffs},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert "4 more" in msg

    def test_footer_shows_schedule(self):
        results = [
            {"sheet": "Model", "tab": "daily", "prod_rows": 1, "staging_rows": 1, "cell_diffs": []},
        ]
        msg = format_diff_message(SAMPLE_DATE, results)
        assert "21:00 CT" in msg
        assert "21:30 CT" in msg
        assert "22:00 CT" in msg
