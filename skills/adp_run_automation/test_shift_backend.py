#!/usr/bin/env python3
"""Tests for shift_backend.parse_xlsx's Details-sheet header handling.

Regression coverage for the 2026-07-10/07-11 BHAGA nightly failures: ADP RUN
inserted new columns ("Show Source", "In Punch Source", "Out Punch Source")
into the Timecard "Details" export, which broke a strict positional header
check even though the rest of parse_xlsx already reads fields by name. See
skills/adp_run_automation/shift_backend.py::parse_xlsx.

Run: python3 -m pytest skills/adp_run_automation/test_shift_backend.py -v
"""
from __future__ import annotations

import pathlib
import tempfile

import openpyxl
import pytest

from skills.adp_run_automation import shift_backend

_LEGACY_HEADER = [
    "Employee Name",
    "Pay Period",
    "Date Range",
    "Total Paid Hours",
    "Date",
    "Start Work",
    "End Work",
    "Regular",
    "Overtime",
    "Doubletime",
    "Details",
    "Notes",
]

# The real header observed in the 2026-07-11/07-12 failed executions (ADP
# inserted "Show Source" / "In Punch Source" / "Out Punch Source", pushing
# "Doubletime"/"Details"/"Notes" further right of what the old check compared).
_NEW_ADP_HEADER = [
    "Employee Name",
    "Pay Period",
    "Date Range",
    "Total Paid Hours",
    "Show Source",
    "Date",
    "Start Work",
    "In Punch Source",
    "End Work",
    "Out Punch Source",
    "Regular",
    "Overtime",
    "Doubletime",
    "Details",
    "Notes",
]

_DATA_ROW = [
    "Smith Jane",  # Employee Name
    "06/29/2026 to 07/12/2026",  # Pay Period
    "06/29/2026 - 07/12/2026",  # Date Range
    "32:15",  # Total Paid Hours
    "Fri 07/10/2026",  # Date
    "9:00 AM",  # Start Work
    "5:00 PM",  # End Work
    "8:00",  # Regular
    "",  # Overtime
    "",  # Doubletime
    "",  # Details
    "",  # Notes
]


def _write_xlsx(tmp_path: pathlib.Path, header: list, data_row_by_name: dict) -> pathlib.Path:
    """Build a Details-sheet xlsx with the given header, populated by column name."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Details"
    ws.append(header)
    ws.append([data_row_by_name.get(col, "") for col in header])
    path = tmp_path / "Timecard-test.xlsx"
    wb.save(path)
    return path


def test_legacy_header_parses(tmp_path):
    """Regression: today's exact column order still parses."""
    path = _write_xlsx(tmp_path, _LEGACY_HEADER, dict(zip(_LEGACY_HEADER, _DATA_ROW)))
    records = shift_backend.parse_xlsx(path)
    assert len(records) == 1
    rec = records[0]
    assert rec["employee_name"] == "Smith Jane"
    assert rec["date"] == "2026-07-10"
    assert rec["in_time"] == "09:00"
    assert rec["out_time"] == "17:00"
    assert rec["regular_hours"] == 8.0
    assert rec["doubletime_hours"] == 0.0


def test_new_adp_layout_parses(tmp_path):
    """ADP's 2026-07 layout (extra + reordered columns) must not raise."""
    row = dict(zip(_LEGACY_HEADER, _DATA_ROW))
    row["Show Source"] = "Punch"
    row["In Punch Source"] = "Punch"
    row["Out Punch Source"] = "Punch"
    path = _write_xlsx(tmp_path, _NEW_ADP_HEADER, row)
    records = shift_backend.parse_xlsx(path)
    assert len(records) == 1
    rec = records[0]
    assert rec["employee_name"] == "Smith Jane"
    assert rec["date"] == "2026-07-10"
    assert rec["regular_hours"] == 8.0


def test_missing_required_column_raises_clear_error(tmp_path):
    """A genuinely missing NEEDED column (e.g. Regular) must error, not silently default."""
    header = [c for c in _LEGACY_HEADER if c != "Regular"]
    row = dict(zip(_LEGACY_HEADER, _DATA_ROW))
    path = _write_xlsx(tmp_path, header, row)
    with pytest.raises(ValueError) as exc_info:
        shift_backend.parse_xlsx(path)
    assert "Regular" in str(exc_info.value)


def test_missing_optional_columns_does_not_raise(tmp_path):
    """Details/Notes are not consumed for hours calc -- absence must not fail the parse."""
    header = [c for c in _LEGACY_HEADER if c not in ("Details", "Notes")]
    row = dict(zip(_LEGACY_HEADER, _DATA_ROW))
    path = _write_xlsx(tmp_path, header, row)
    records = shift_backend.parse_xlsx(path)
    assert len(records) == 1
    assert records[0]["employee_name"] == "Smith Jane"
