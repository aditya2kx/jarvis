#!/usr/bin/env python3
"""Tests for lifecycle.py — pct helpers and stage/substep mapping."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lifecycle as lc


class TestStagesStructure(unittest.TestCase):
    def test_five_stages(self):
        self.assertEqual(len(lc.STAGES), 5)

    def test_twelve_substeps_total(self):
        # 4 (align) + 1 (plan) + 2 (build) + 3 (ship) + 2 (verify-learn) = 12
        self.assertEqual(len(lc.all_substeps()), 12)

    def test_stage_names(self):
        names = [s.name for s in lc.STAGES]
        self.assertIn("align", names)
        self.assertIn("plan", names)
        self.assertIn("build", names)
        self.assertIn("ship", names)
        self.assertIn("verify-learn", names)

    def test_each_substep_has_driver(self):
        for sub in lc.all_substeps():
            self.assertIn(sub.driver, {"operator", "agent"},
                          f"Substep {sub.name!r} has invalid driver {sub.driver!r}")

    def test_operator_substeps(self):
        self.assertIn("specify", lc.OPERATOR_SUBSTEPS)
        self.assertIn("jam", lc.OPERATOR_SUBSTEPS)
        self.assertIn("define-evidence", lc.OPERATOR_SUBSTEPS)
        self.assertIn("merge", lc.OPERATOR_SUBSTEPS)

    def test_agent_substeps_not_in_operator_set(self):
        self.assertNotIn("setup", lc.OPERATOR_SUBSTEPS)
        self.assertNotIn("plan", lc.OPERATOR_SUBSTEPS)
        self.assertNotIn("implement", lc.OPERATOR_SUBSTEPS)
        self.assertNotIn("verify", lc.OPERATOR_SUBSTEPS)


class TestHelpers(unittest.TestCase):
    def test_substep_index_known(self):
        idx = lc.substep_index("specify")
        self.assertEqual(idx, 0)

    def test_substep_index_last(self):
        steps = lc.all_substeps()
        idx = lc.substep_index(steps[-1].name)
        self.assertEqual(idx, len(steps) - 1)

    def test_substep_index_unknown_raises(self):
        with self.assertRaises(ValueError):
            lc.substep_index("nonexistent")

    def test_next_substep(self):
        nxt = lc.next_substep("specify")
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.name, "setup")

    def test_next_substep_last_returns_none(self):
        last = lc.all_substeps()[-1]
        self.assertIsNone(lc.next_substep(last.name))

    def test_stage_of_specify(self):
        stage = lc.stage_of("specify")
        self.assertEqual(stage.name, "align")

    def test_stage_of_merge(self):
        stage = lc.stage_of("merge")
        self.assertEqual(stage.name, "ship")

    def test_stage_of_unknown_raises(self):
        with self.assertRaises(ValueError):
            lc.stage_of("no-such-substep")


class TestProgress(unittest.TestCase):
    def test_overall_pct_zero(self):
        self.assertEqual(lc.overall_pct(set()), 0)

    def test_overall_pct_all_done(self):
        all_names = {s.name for s in lc.all_substeps()}
        self.assertEqual(lc.overall_pct(all_names), 100)

    def test_overall_pct_partial(self):
        # First 4 of 12 done → 33%
        done = {s.name for s in lc.all_substeps()[:4]}
        pct = lc.overall_pct(done)
        self.assertEqual(pct, int(4 / 12 * 100))

    def test_stage_pct_zero(self):
        self.assertEqual(lc.stage_pct("align", set()), 0)

    def test_stage_pct_full(self):
        align_substeps = {s.name for s in lc.STAGES[0].substeps}
        self.assertEqual(lc.stage_pct("align", align_substeps), 100)

    def test_stage_pct_unknown_raises(self):
        with self.assertRaises(ValueError):
            lc.stage_pct("nonexistent", set())

    def test_current_substep_empty(self):
        cur = lc.current_substep(set())
        self.assertIsNotNone(cur)
        self.assertEqual(cur.name, "specify")

    def test_current_substep_all_done(self):
        all_names = {s.name for s in lc.all_substeps()}
        cur = lc.current_substep(all_names)
        self.assertIsNone(cur)


class TestBriefLadder(unittest.TestCase):
    def test_brief_ladder_contains_all_stages(self):
        text = lc.brief_ladder_text()
        for stage in lc.STAGES:
            self.assertIn(stage.name.upper(), text)

    def test_brief_ladder_contains_operator_tag(self):
        text = lc.brief_ladder_text()
        self.assertIn("OPERATOR-RESERVED", text)

    def test_brief_ladder_contains_self_drive_note(self):
        text = lc.brief_ladder_text()
        self.assertIn("phase_state.py advance", text)


if __name__ == "__main__":
    unittest.main()
