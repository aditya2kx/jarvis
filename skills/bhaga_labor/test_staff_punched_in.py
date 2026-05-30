"""Tests for point-in-time staff punched-in counts."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.bhaga_labor.staff_punched_in import (
    classify_employee_bucket,
    count_staff_punched_in_at,
    index_punches_by_date,
)

_FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "golden_staff_day.json"


def _load_golden() -> dict:
    return json.loads(_FIXTURE.read_text())


class ClassifyBucketTests(unittest.TestCase):
    def test_manager_is_fulltime(self):
        rates = [{"employee_name": "Krause, Lindsay", "is_salaried": False, "excluded_from_labor_pct": True}]
        by_name = {r["employee_name"]: r for r in rates}
        self.assertEqual(
            classify_employee_bucket("Krause, Lindsay", by_name, {"Krause, Lindsay"}),
            "fulltime",
        )

    def test_barista_is_hourly(self):
        rates = [{"employee_name": "Alvarez, Sebastian", "is_salaried": False, "excluded_from_labor_pct": False}]
        by_name = {r["employee_name"]: r for r in rates}
        self.assertEqual(
            classify_employee_bucket("Alvarez, Sebastian", by_name, {"Krause, Lindsay"}),
            "hourly",
        )


class CountStaffPunchedInTests(unittest.TestCase):
    def setUp(self) -> None:
        self.excluded = {"Krause, Lindsay"}
        self.rates = [
            {"employee_name": "Krause, Lindsay", "is_salaried": False, "excluded_from_labor_pct": True},
            {"employee_name": "Alvarez, Sebastian", "is_salaried": False, "excluded_from_labor_pct": False},
            {"employee_name": "Guerrero, Amy", "is_salaried": False, "excluded_from_labor_pct": False},
        ]

    def test_two_hourly_plus_manager_at_rush(self):
        punches = [
            {"date": "2026-05-26", "employee_name": "Alvarez, Sebastian", "in_time": "08:00", "out_time": "14:00"},
            {"date": "2026-05-26", "employee_name": "Guerrero, Amy", "in_time": "08:00", "out_time": "14:00"},
            {"date": "2026-05-26", "employee_name": "Krause, Lindsay", "in_time": "08:00", "out_time": "14:00"},
        ]
        c = count_staff_punched_in_at(
            item_sold_at_local="2026-05-26T12:15:00",
            punches=punches,
            wage_rates=self.rates,
            excluded_from_tip_pool=self.excluded,
        )
        self.assertEqual(c["staff_punched_in_hourly_count"], 2)
        self.assertEqual(c["staff_punched_in_fulltime_count"], 1)
        self.assertEqual(c["staff_punched_in_total_count"], 3)

    def test_manager_only_early_line(self):
        punches = [
            {"date": "2026-05-26", "employee_name": "Krause, Lindsay", "in_time": "06:00", "out_time": "07:00"},
        ]
        c = count_staff_punched_in_at(
            item_sold_at_local="2026-05-26T06:30:00",
            punches=punches,
            wage_rates=self.rates,
            excluded_from_tip_pool=self.excluded,
        )
        self.assertEqual(c["staff_punched_in_hourly_count"], 0)
        self.assertEqual(c["staff_punched_in_fulltime_count"], 1)
        self.assertEqual(c["staff_punched_in_total_count"], 1)

    def test_split_shift_only_covering_punch_counts(self):
        punches = [
            {"date": "2026-05-26", "employee_name": "Alvarez, Sebastian", "in_time": "06:00", "out_time": "10:00"},
            {"date": "2026-05-26", "employee_name": "Alvarez, Sebastian", "in_time": "14:00", "out_time": "18:00"},
        ]
        c = count_staff_punched_in_at(
            item_sold_at_local="2026-05-26T15:00:00",
            punches=punches,
            wage_rates=self.rates,
            excluded_from_tip_pool=self.excluded,
        )
        self.assertEqual(c["staff_punched_in_hourly_count"], 1)
        self.assertEqual(c["staff_punched_in_total_count"], 1)

    def test_no_punches_zero_counts(self):
        c = count_staff_punched_in_at(
            item_sold_at_local="2026-05-26T12:00:00",
            punches=[],
            wage_rates=self.rates,
            excluded_from_tip_pool=self.excluded,
        )
        self.assertEqual(c["staff_punched_in_total_count"], 0)


class GoldenStaffDayE2ETests(unittest.TestCase):
    """S1: golden staffing day — five item lines with expected counts."""

    def test_golden_table(self):
        g = _load_golden()
        punches = g["punches"]
        rates = g["wage_rates"]
        excluded = set(g["excluded_from_tip_pool"])
        by_date = index_punches_by_date(punches)

        for case in g["item_lines"]:
            got = count_staff_punched_in_at(
                item_sold_at_local=case["item_sold_at_local"],
                punches=punches,
                wage_rates=rates,
                excluded_from_tip_pool=excluded,
                punches_by_date=by_date,
            )
            self.assertEqual(
                got,
                case["expected_counts"],
                msg=case.get("label", case["item_sold_at_local"]),
            )


if __name__ == "__main__":
    unittest.main()
