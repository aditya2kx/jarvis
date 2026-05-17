#!/usr/bin/env python3
"""Unit tests for skills.tip_pool_allocation.

Run:
    python skills/tip_pool_allocation/test_adapter.py

Covers every edge case enumerated in adapter.allocate docstring + the
non-negotiable invariants from bhaga.md rules 5 and 11.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from skills.tip_pool_allocation.adapter import (
    allocate,
    _largest_remainder_distribute,
)


class LargestRemainderTests(unittest.TestCase):
    def test_basic_two_way_split(self):
        # 100c between two equal workers → 50/50
        d = _largest_remainder_distribute(100, [("a", 1.0), ("b", 1.0)])
        self.assertEqual(d, {"a": 50, "b": 50})

    def test_three_equal_residual_lex_order(self):
        # 100c across 3 equal workers → [34, 33, 33], with largest going
        # to lexicographically first key for determinism.
        d = _largest_remainder_distribute(100, [("alice", 1.0), ("bob", 1.0), ("charlie", 1.0)])
        self.assertEqual(sum(d.values()), 100)
        self.assertEqual(d["alice"], 34)   # ties broken by key
        self.assertEqual(d["bob"], 33)
        self.assertEqual(d["charlie"], 33)

    def test_unequal_weights_proportional(self):
        # 100c split 3:1 → 75/25
        d = _largest_remainder_distribute(100, [("a", 3.0), ("b", 1.0)])
        self.assertEqual(d, {"a": 75, "b": 25})

    def test_cent_conservation_arbitrary(self):
        # Sum of shares MUST equal the pool, exactly, for any weight set.
        import random
        random.seed(42)
        for _ in range(200):
            pool = random.randint(0, 100_000)
            n = random.randint(1, 12)
            weights = [(f"e{i}", random.uniform(0, 10)) for i in range(n)]
            d = _largest_remainder_distribute(pool, weights)
            self.assertEqual(sum(d.values()), pool, f"pool={pool} weights={weights}")

    def test_zero_pool(self):
        d = _largest_remainder_distribute(0, [("a", 5.0), ("b", 3.0)])
        self.assertEqual(d, {"a": 0, "b": 0})

    def test_zero_weights(self):
        d = _largest_remainder_distribute(100, [("a", 0), ("b", 0)])
        self.assertEqual(d, {"a": 0, "b": 0})

    def test_single_worker_gets_all(self):
        d = _largest_remainder_distribute(12345, [("solo", 7.5)])
        self.assertEqual(d, {"solo": 12345})

    def test_determinism(self):
        # Same inputs → same outputs, always.
        weights = [("charlie", 1.3), ("alice", 2.1), ("bob", 0.7)]
        runs = [_largest_remainder_distribute(12345, weights) for _ in range(10)]
        for r in runs[1:]:
            self.assertEqual(r, runs[0])


class PoolByDayFairnessTests(unittest.TestCase):
    """The critical invariant: NEVER pool the period's tips against period's hours."""

    def test_high_tip_day_rewards_worker_who_was_there(self):
        """Alice worked only on the high-tip day; Bob only on the low-tip day.
        Under pool-by-day, Alice gets more despite equal total hours."""
        tips = {"2026-04-01": 10000, "2026-04-02": 1000}  # $100 vs $10 day
        hours = {
            ("alice", "2026-04-01"): 5.0,
            ("bob",   "2026-04-02"): 5.0,
        }
        result = allocate(tips, hours)
        per_emp = {p.employee: p.total_tip_cents for p in result.per_period}
        self.assertEqual(per_emp["alice"], 10000)
        self.assertEqual(per_emp["bob"], 1000)
        # If we had POOLED incorrectly (same total hours → 50/50 of 11000) they'd both get 5500. No.

    def test_fair_split_within_single_day(self):
        # $20 pool, 2 equal workers → $10 each
        tips = {"2026-04-01": 2000}
        hours = {("alice", "2026-04-01"): 4.0, ("bob", "2026-04-01"): 4.0}
        r = allocate(tips, hours)
        shares = {p.employee: p.share_cents for p in r.per_day}
        self.assertEqual(shares, {"alice": 1000, "bob": 1000})

    def test_weighted_split_within_single_day(self):
        # $15 pool, Alice 4h, Bob 2h → Alice $10, Bob $5
        tips = {"2026-04-01": 1500}
        hours = {("alice", "2026-04-01"): 4.0, ("bob", "2026-04-01"): 2.0}
        r = allocate(tips, hours)
        shares = {p.employee: p.share_cents for p in r.per_day}
        self.assertEqual(shares, {"alice": 1000, "bob": 500})


class EdgeCaseTests(unittest.TestCase):
    def test_tips_with_no_hours_flagged(self):
        tips = {"2026-04-01": 5000}
        hours: dict = {}
        r = allocate(tips, hours)
        self.assertEqual(r.per_day, [])
        self.assertEqual(len(r.flags), 1)
        self.assertEqual(r.flags[0].issue, "tips_with_no_hours")
        self.assertEqual(r.flags[0].tip_cents, 5000)

    def test_hours_with_no_tips_flagged_zero_shares_emitted(self):
        tips = {"2026-04-01": 0}
        hours = {("alice", "2026-04-01"): 4.0, ("bob", "2026-04-01"): 3.0}
        r = allocate(tips, hours)
        self.assertEqual(len(r.per_day), 2)
        for p in r.per_day:
            self.assertEqual(p.share_cents, 0)
        self.assertEqual(len(r.flags), 1)
        self.assertEqual(r.flags[0].issue, "hours_with_no_tips")

    def test_quiet_day_no_tips_no_hours_no_flag(self):
        tips = {"2026-04-01": 0}
        hours: dict = {}
        r = allocate(tips, hours)
        self.assertEqual(r.per_day, [])
        self.assertEqual(r.flags, [])
        self.assertEqual(r.per_period, [])

    def test_employee_zero_hours_on_date_omitted(self):
        # If we explicitly pass 0 hours for an employee, they should NOT get a
        # per-day row for that date.
        tips = {"2026-04-01": 1000}
        hours = {("alice", "2026-04-01"): 5.0, ("bob", "2026-04-01"): 0.0}
        r = allocate(tips, hours)
        self.assertEqual(len(r.per_day), 1)
        self.assertEqual(r.per_day[0].employee, "alice")
        self.assertEqual(r.per_day[0].share_cents, 1000)
        # Bob still appears in per_period (he's a known employee) with 0 hrs/tips
        per_emp = {p.employee for p in r.per_period}
        self.assertEqual(per_emp, {"alice", "bob"})

    def test_non_integer_tip_cents_raises(self):
        with self.assertRaises(ValueError):
            allocate({"2026-04-01": 12.5}, {("alice", "2026-04-01"): 5.0})

    def test_negative_tips_raises(self):
        with self.assertRaises(ValueError):
            allocate({"2026-04-01": -500}, {("alice", "2026-04-01"): 5.0})

    def test_negative_hours_raises(self):
        with self.assertRaises(ValueError):
            allocate({"2026-04-01": 500}, {("alice", "2026-04-01"): -1.0})

    def test_empty_inputs(self):
        r = allocate({}, {})
        self.assertEqual(r.per_day, [])
        self.assertEqual(r.per_period, [])
        self.assertEqual(r.flags, [])


class InvariantTests(unittest.TestCase):
    def test_per_day_sum_equals_pool(self):
        """For every productive day, sum of per-employee shares == pool exactly."""
        tips = {
            "2026-04-01": 12345,
            "2026-04-02": 0,
            "2026-04-03": 99,
            "2026-04-04": 77777,
        }
        hours = {
            ("alice", "2026-04-01"): 4.0,
            ("bob",   "2026-04-01"): 3.0,
            ("alice", "2026-04-03"): 2.5,
            ("charlie", "2026-04-03"): 2.5,
            ("alice", "2026-04-04"): 8.0,
            ("bob",   "2026-04-04"): 6.0,
            ("charlie", "2026-04-04"): 4.0,
        }
        r = allocate(tips, hours)
        by_date_sum: dict[str, int] = {}
        for p in r.per_day:
            by_date_sum[p.date] = by_date_sum.get(p.date, 0) + p.share_cents
        self.assertEqual(by_date_sum.get("2026-04-01"), 12345)
        self.assertEqual(by_date_sum.get("2026-04-03"), 99)
        self.assertEqual(by_date_sum.get("2026-04-04"), 77777)

    def test_per_period_equals_sum_of_per_day(self):
        tips = {"2026-04-01": 1000, "2026-04-02": 2000}
        hours = {
            ("alice", "2026-04-01"): 4.0,
            ("bob",   "2026-04-01"): 2.0,
            ("alice", "2026-04-02"): 3.0,
            ("bob",   "2026-04-02"): 5.0,
        }
        r = allocate(tips, hours)
        from_per_day: dict[str, int] = {}
        from_per_day_hrs: dict[str, float] = {}
        for p in r.per_day:
            from_per_day[p.employee] = from_per_day.get(p.employee, 0) + p.share_cents
            from_per_day_hrs[p.employee] = from_per_day_hrs.get(p.employee, 0) + p.hours
        for pp in r.per_period:
            self.assertEqual(from_per_day.get(pp.employee, 0), pp.total_tip_cents)
            self.assertAlmostEqual(from_per_day_hrs.get(pp.employee, 0), pp.total_hours, places=6)


class RealWorldTests(unittest.TestCase):
    """Grounded in the actual week-of-3/23 Square data we pulled 2026-04-19."""

    def test_austin_week_of_3_23(self):
        # Actual tip totals for 3/23–3/29/2026 from skills/square_tips dashboard_backend.
        tips = {
            "2026-03-23": 3840, "2026-03-24": 3588, "2026-03-25": 1792,
            "2026-03-26": 4466, "2026-03-27": 3856, "2026-03-28": 5751,
            "2026-03-29": 5554,
        }
        # Synthetic hours — two hypothetical employees rotating shifts.
        hours = {
            ("emp_maria", "2026-03-23"): 7.5,
            ("emp_james", "2026-03-23"): 4.0,
            ("emp_maria", "2026-03-24"): 3.5,
            ("emp_james", "2026-03-24"): 8.0,
            ("emp_maria", "2026-03-25"): 5.0,
            ("emp_james", "2026-03-25"): 5.0,
            ("emp_maria", "2026-03-26"): 4.0,
            ("emp_james", "2026-03-26"): 4.0,
            ("emp_maria", "2026-03-27"): 6.0,
            ("emp_james", "2026-03-27"): 2.0,
            ("emp_maria", "2026-03-28"): 8.0,
            ("emp_james", "2026-03-28"): 6.0,
            ("emp_maria", "2026-03-29"): 5.0,
            ("emp_james", "2026-03-29"): 7.0,
        }
        r = allocate(tips, hours)

        # Exact conservation: week's total shares == week's total tips.
        total_shares = sum(p.share_cents for p in r.per_day)
        self.assertEqual(total_shares, sum(tips.values()))
        self.assertEqual(total_shares, 28847)   # $288.47 matches dashboard

        # No flags — every day has both tips and hours.
        self.assertEqual(r.flags, [])

        # Per-day conservation for each date.
        by_date: dict[str, int] = {}
        for p in r.per_day:
            by_date[p.date] = by_date.get(p.date, 0) + p.share_cents
        for date, pool in tips.items():
            self.assertEqual(by_date[date], pool, f"date={date}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
