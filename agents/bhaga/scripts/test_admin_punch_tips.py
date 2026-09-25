"""Admin punches are paid but earn no tips (Issue #343).

The operator adds a separate ADP punch with a note containing "admin"; ADP
prefixes the note with the editor's name. Notes below are the real 2026-09-22 /
09-25 Timecard shapes with the editor name replaced.

Run:
    python3 -m pytest agents/bhaga/scripts/test_admin_punch_tips.py -q
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import update_model_sheet as ums

ADMIN_NOTE = "Manager, Some   Admin Work"
ADMIN_NOTE_LOWER = "Manager, Some   Admin work"
OTHER_NOTE = "Manager, Some   Updated missing close out as per scheduled shift timings"
KW = ["admin"]


class TestPunchNoteMatches(unittest.TestCase):
    def test_admin_anywhere_any_case(self):
        for note in (ADMIN_NOTE, ADMIN_NOTE_LOWER, "ADMIN", "did admin stuff"):
            self.assertTrue(ums.punch_note_matches(note, KW), note)

    def test_other_notes_do_not_match(self):
        for note in (OTHER_NOTE, "", None):
            self.assertFalse(ums.punch_note_matches(note, KW), note)

    def test_no_keywords_disables(self):
        self.assertFalse(ums.punch_note_matches(ADMIN_NOTE, []))

    def test_any_of_several_keywords(self):
        self.assertTrue(ums.punch_note_matches("Inventory count", ["admin", "inventory"]))


def _punch(emp, date, tin, tout, note=""):
    return {"employee_name": emp, "date": date, "in_time": tin, "out_time": tout, "note": note}


class TestWindowsFromPunches(unittest.TestCase):
    def test_only_matching_punches_become_windows(self):
        punches = [
            _punch("Johnson, Dolce", "2026-09-22", "07:00", "15:00"),
            _punch("Johnson, Dolce", "2026-09-22", "15:00", "15:30", ADMIN_NOTE),
            _punch("Johnson, Dolce", "2026-09-23", "07:00", "15:00", OTHER_NOTE),
        ]
        self.assertEqual(
            ums.tip_exempt_windows_from_punches(punches, KW),
            {("Johnson, Dolce", "2026-09-22"): [("15:00", "15:30")]},
        )

    def test_blank_keywords_yield_nothing(self):
        punches = [_punch("Johnson, Dolce", "2026-09-22", "15:00", "15:30", ADMIN_NOTE)]
        self.assertEqual(ums.tip_exempt_windows_from_punches(punches, []), {})


PERIOD = {"start": "2026-09-21", "end": "2026-10-04", "check_dates": [], "is_open": True, "variants": []}
SHIFTS = [
    # adp_shifts day rollup: first in → last out, hours summed across punches.
    {"employee_name": "Johnson, Dolce", "date": "2026-09-22",
     "in_time": "07:00", "out_time": "15:30", "total_hours": 8.5},
    {"employee_name": "Other, Pat", "date": "2026-09-22",
     "in_time": "10:00", "out_time": "18:00", "total_hours": 8.0},
]
TXNS = [{"date_local": "2026-09-22", "tip_cents": 10000}]
WINDOWS = {("Johnson, Dolce", "2026-09-22"): [("15:00", "15:30")]}


def _run(**kw):
    return ums.build_period_results(
        periods=[PERIOD], shifts=SHIFTS, txns=TXNS, actuals={},
        excluded=set(), square_data_start="2026-03-01", **kw,
    )[0]


class TestSeptember22Reproduction(unittest.TestCase):
    def test_admin_half_hour_earns_no_tips_and_pool_is_conserved(self):
        r = _run(punch_exempt_windows=WINDOWS)
        self.assertAlmostEqual(r["per_period_hours"]["Johnson, Dolce"], 8.0)
        self.assertAlmostEqual(r["per_period_hours"]["Other, Pat"], 8.0)
        self.assertEqual(r["per_period_ours"]["Johnson, Dolce"], 5000)
        self.assertEqual(r["per_period_ours"]["Other, Pat"], 5000)
        self.assertEqual(sum(r["per_period_ours"].values()), 10000)

    def test_legacy_without_windows_is_unchanged(self):
        self.assertEqual(_run(), _run(punch_exempt_windows={}))
        self.assertEqual(_run(), _run(punch_exempt_windows=None))
        self.assertAlmostEqual(_run()["per_period_hours"]["Johnson, Dolce"], 8.5)

    def test_admin_window_inside_tip_exemption_is_not_subtracted_twice(self):
        ts = {("Johnson, Dolce", "2026-09-22"): {"exempt_start": "14:30", "exempt_end": "15:30"}}
        r = _run(training_shifts=ts, punch_exempt_windows=WINDOWS)
        self.assertAlmostEqual(r["per_period_hours"]["Johnson, Dolce"], 7.5)
        self.assertEqual(sum(r["per_period_ours"].values()), 10000)

    def test_daily_team_hours_drop_the_admin_time(self):
        import datetime
        txn = {"date_local": "2026-09-22", "tip_cents": 10000, "gross_sales_cents": 50000,
               "discount_cents": 0, "total_collected_cents": 60000, "event_type": "Payment"}
        _, summary = ums.build_daily_rows(
            txns=[txn], shifts=SHIFTS, excluded=set(), punch_exempt_windows=WINDOWS,
            now_ct=datetime.datetime(2026, 9, 25, 12, 0),
        )
        self.assertAlmostEqual(summary["2026-09-22"]["team_hours"], 16.0)


class TestMaterializeKeywordConfig(unittest.TestCase):
    def _kw(self, value):
        from agents.bhaga.scripts import materialize_model_bq as m
        with mock.patch("core.store_config.get_config", return_value=value):
            return m.load_tip_exempt_note_keywords("palmetto")

    def test_missing_row_defaults_to_admin(self):
        self.assertEqual(self._kw(None), ["admin"])

    def test_blank_row_disables(self):
        self.assertEqual(self._kw(""), [])

    def test_semicolon_list(self):
        self.assertEqual(self._kw(" admin ; inventory ;"), ["admin", "inventory"])


if __name__ == "__main__":
    unittest.main()
