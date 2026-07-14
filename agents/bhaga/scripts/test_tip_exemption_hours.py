"""Unit tests for tip-exemption overlap hour helpers (Issue #167).

Run:
    python3 -m pytest agents/bhaga/scripts/test_tip_exemption_hours.py -q
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from agents.bhaga.scripts import update_model_sheet as ums


class TestOverlapHours(unittest.TestCase):
    def test_full_overlap_inside_shift(self):
        # 13:30–20:30 (7h) ∩ 18:00–18:30 = 0.5h
        self.assertAlmostEqual(
            ums._overlap_hours("13:30", "20:30", "18:00", "18:30"), 0.5,
        )

    def test_no_overlap(self):
        self.assertEqual(ums._overlap_hours("13:30", "20:30", "10:00", "10:30"), 0.0)

    def test_inverted_window(self):
        self.assertEqual(ums._overlap_hours("13:30", "20:30", "18:30", "18:00"), 0.0)

    def test_malformed(self):
        self.assertEqual(ums._overlap_hours("", "20:30", "18:00", "18:30"), 0.0)
        self.assertEqual(ums._overlap_hours("13:30", "20:30", "bad", "18:30"), 0.0)

    def test_window_clips_to_shift_bounds(self):
        # exempt starts before shift → clip to in_time
        self.assertAlmostEqual(
            ums._overlap_hours("13:30", "20:30", "13:00", "14:00"), 0.5,
        )


class TestTipHoursAfterExemption(unittest.TestCase):
    def test_whole_day_zeros(self):
        self.assertEqual(
            ums._tip_hours_after_exemption(
                7.0, "13:30", "20:30",
                whole_day=True, exempt_start=None, exempt_end=None,
            ),
            0.0,
        )

    def test_partial_window_subtracts_overlap(self):
        # 7.0 total − 0.5 overlap = 6.5
        self.assertAlmostEqual(
            ums._tip_hours_after_exemption(
                7.0, "13:30", "20:30",
                whole_day=False, exempt_start="18:00", exempt_end="18:30",
            ),
            6.5,
        )

    def test_missing_window_unchanged(self):
        self.assertEqual(
            ums._tip_hours_after_exemption(
                7.0, "13:30", "20:30",
                whole_day=False, exempt_start=None, exempt_end=None,
            ),
            7.0,
        )

    def test_orphan_window_no_overlap_leaves_hours(self):
        # Window outside shift clock → 0 overlap → full tip hours
        self.assertEqual(
            ums._tip_hours_after_exemption(
                6.5, "13:30", "20:00",
                whole_day=False, exempt_start="10:00", exempt_end="10:30",
            ),
            6.5,
        )

    def test_window_covers_entire_shift(self):
        self.assertEqual(
            ums._tip_hours_after_exemption(
                4.0, "10:00", "14:00",
                whole_day=False, exempt_start="09:00", exempt_end="15:00",
            ),
            0.0,
        )


class TestEligibleTipHoursForShift(unittest.TestCase):
    def _shift(self, **kw):
        base = {
            "employee_name": "Doe, Jane",
            "date": "2026-07-08",
            "in_time": "13:30",
            "out_time": "20:30",
            "total_hours": 7.0,
        }
        base.update(kw)
        return base

    def test_whole_day_overlay_dict(self):
        ts = {
            ("Doe, Jane", "2026-07-08"): {
                "exempt_start": None, "exempt_end": None, "note": "training",
            },
        }
        self.assertEqual(
            ums._eligible_tip_hours_for_shift(
                self._shift(), permanent=set(), training_through={}, training_shifts=ts,
            ),
            0.0,
        )

    def test_partial_window_subtracts(self):
        ts = {
            ("Doe, Jane", "2026-07-08"): {
                "exempt_start": "18:00", "exempt_end": "18:30", "note": "Meeting",
            },
        }
        self.assertAlmostEqual(
            ums._eligible_tip_hours_for_shift(
                self._shift(), permanent=set(), training_through={}, training_shifts=ts,
            ),
            6.5,
        )

    def test_orphan_window_no_overlap_keeps_hours(self):
        ts = {
            ("Doe, Jane", "2026-07-08"): {
                "exempt_start": "10:00", "exempt_end": "10:30", "note": "Meeting",
            },
        }
        self.assertEqual(
            ums._eligible_tip_hours_for_shift(
                self._shift(), permanent=set(), training_through={}, training_shifts=ts,
            ),
            7.0,
        )

    def test_legacy_set_overlay(self):
        ts = {("Doe, Jane", "2026-07-08")}
        self.assertEqual(
            ums._eligible_tip_hours_for_shift(
                self._shift(), permanent=set(), training_through={}, training_shifts=ts,
            ),
            0.0,
        )


class TestBuildPeriodResultsPartialWindow(unittest.TestCase):
    def test_partial_hours_enter_allocator(self):
        periods = [{
            "start": "2026-07-01", "end": "2026-07-14",
            "check_dates": [], "is_open": True, "variants": [],
        }]
        shifts = [{
            "employee_name": "Doe, Jane", "date": "2026-07-08",
            "in_time": "13:30", "out_time": "20:30", "total_hours": 7.0,
        }, {
            "employee_name": "Bob, B", "date": "2026-07-08",
            "in_time": "13:30", "out_time": "20:30", "total_hours": 7.0,
        }]
        txns = [{"date_local": "2026-07-08", "tip_cents": 1400}]
        ts = {
            ("Doe, Jane", "2026-07-08"): {
                "exempt_start": "18:00", "exempt_end": "18:30", "note": "Meeting",
            },
        }
        results = ums.build_period_results(
            periods=periods, shifts=shifts, txns=txns, actuals={},
            excluded=set(), square_data_start="2026-03-01",
            training_shifts=ts,
        )
        hours = results[0]["per_period_hours"]
        self.assertAlmostEqual(hours["Doe, Jane"], 6.5)
        self.assertAlmostEqual(hours["Bob, B"], 7.0)
        # Pool conserved
        total_tips = sum(results[0]["per_period_ours"].values())
        self.assertEqual(total_tips, 1400)


if __name__ == "__main__":
    unittest.main()
