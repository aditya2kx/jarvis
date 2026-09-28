"""Tests for core/order_reco_reference.py (Issue #350)."""
from __future__ import annotations

import json
import math
import pathlib
import unittest
from datetime import date

from core.order_reco_reference import (
    ItemInput,
    RecoInputs,
    _water_fill,
    bq_round,
    compute_reco,
)

_GOLDEN = json.loads(
    (pathlib.Path(__file__).parent / "testdata" / "order_reco_waterfill_golden.json").read_text()
)


class TestWaterFillGolden(unittest.TestCase):
    """Same fixture as apps/operator-console/__tests__/water-fill.test.ts."""

    def test_every_case(self):
        for case in _GOLDEN["cases"]:
            with self.subTest(case["name"]):
                bases = case.get("bases", _GOLDEN["bases"])
                on_hand = {b["item"]: b["onHand"] for b in bases}
                avg = {b["item"]: b["avgPerDay"] for b in bases}
                pins = case["pins"]
                budget = max(math.floor(case["capacity"] - sum(on_hand.values())) - sum(pins.values()), 0)
                self.assertEqual(budget, case["budget"])
                est, tie = _water_fill(on_hand, avg, budget, set(pins))
                got = {i: pins.get(i, est.get(i, 0)) for i in on_hand}
                self.assertEqual(got, case["expected"])
                self.assertEqual(tie, case["cutoffTie"])

    def test_operator_story_2026_10_09(self):
        before, after, cap112 = (_GOLDEN["cases"][i]["expected"] for i in range(3))
        self.assertEqual(sum(before.values()), 29)
        # Pins net +3 tubs, so unpinned Matcha/Mango give up 3 under the same capacity.
        self.assertEqual((after["Matcha"], after["Mango"]), (6, 5))
        self.assertEqual(sum(after.values()), 29)
        self.assertEqual(sum(cap112.values()), 31)


class TestComputeReco(unittest.TestCase):
    def _inputs(self, **kw) -> RecoInputs:
        base = dict(
            as_of=date(2026, 9, 28),
            capacity=40,
            dates=[date(2026, 10, 2), date(2026, 10, 9)],
            items=[ItemInput("Açaí", 20.0, 1.5), ItemInput("Mango", 10.0, 0.8), ItemInput("Blade", 0.0, 1.0)],
        )
        base.update(kw)
        return RecoInputs(**base)

    def test_capacity_is_a_hard_cap_per_date(self):
        for r in compute_reco(self._inputs()).rows:
            if r.item == "TOTAL":
                self.assertLessEqual(r.on_hand + r.order_tubs, 40 + 1e-9)

    def test_actuals_win_the_whole_date(self):
        inputs = self._inputs(actuals={(date(2026, 10, 2), "Açaí"): 5.0},
                              pins={(date(2026, 10, 2), "Mango"): 9})
        slot1 = [r for r in compute_reco(inputs).rows if r.slot == 1]
        self.assertTrue(all(r.source == "Actuals" for r in slot1))
        self.assertEqual({r.item: r.order_tubs for r in slot1 if r.item != "TOTAL"},
                         {"Açaí": 5, "Mango": 0, "Blade": 0})

    def test_pins_are_manual_and_reduce_the_budget(self):
        inputs = self._inputs(pins={(date(2026, 10, 9), "Açaí"): 4})
        slot2 = {r.item: r for r in compute_reco(inputs).rows if r.slot == 2}
        self.assertEqual(slot2["Açaí"].source, "Manual")
        self.assertEqual(slot2["Açaí"].order_tubs, 4)
        self.assertEqual(slot2["Mango"].source, "Estimated")

    def test_slot2_chains_from_rounded_slot1(self):
        rows = compute_reco(self._inputs()).rows
        s1 = {r.item: r for r in rows if r.slot == 1}
        s2 = {r.item: r for r in rows if r.slot == 2}
        expect = max(s1["Mango"].on_hand + s1["Mango"].order_tubs - 7 * 0.8, 0)
        self.assertAlmostEqual(s2["Mango"].on_hand, bq_round(expect, 2))

    def test_blade_is_never_water_filled(self):
        self.assertTrue(all(r.order_tubs == 0 for r in compute_reco(self._inputs()).rows if r.item == "Blade"))

    def test_bq_round_is_half_away_from_zero(self):
        self.assertEqual(bq_round(2.5), 3.0)
        self.assertEqual(bq_round(0.125, 2), 0.13)
        self.assertEqual(bq_round(-2.5), -3.0)


if __name__ == "__main__":
    unittest.main()
