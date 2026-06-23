#!/usr/bin/env python3
"""Tests for dogfood_lifecycle.py — network-free (injected fake `run`, synthetic state)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dogfood_lifecycle as dl
import lifecycle as lc
import unittest


def _fake_run(returns):
    """Return a fake `run` callable that yields (rc, out, err) from `returns` in order."""
    seq = list(returns)
    calls = []

    def run(cmd, cwd=None, timeout=600):
        calls.append(cmd)
        return seq.pop(0) if seq else (0, "", "")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _complete_state() -> dict:
    """A synthetic state where the dogfood walked every substep correctly."""
    records = []
    for s in lc.all_substeps():
        rec = {"substep": s.name, "driver": s.driver, "marker": "HARNESS-DRIVEN",
               "actions": [], "commands": [], "gate_refused": False,
               "gate_approved": False, "ok": True}
        if s.name in dl.SIMULATED_GATES:
            rec["marker"] = "OPERATOR-SIMULATED"
            rec["gate_refused"] = True
            rec["gate_approved"] = True
        if s.name == "specify":
            rec["marker"] = "SEEDED"
        if s.name == "merge":
            rec["marker"] = "OPERATOR-REAL"
        records.append(rec)
    return {
        "branch": "dogfood/lifecycle-x", "issue": 101, "dummy_pr": 202,
        "dummy_pr_state": "MERGED", "rereview_misfire": False,
        "issue_closed": True, "records": records,
    }


class TestOperatorGateDemo(unittest.TestCase):
    def test_demo_gate_records_refusal_then_approval(self):
        # advance(no approval)->rc1 ; gh add-label->rc0 ; advance(--operator-approved)->rc0
        run = _fake_run([(1, "operator-reserved; awaiting", ""), (0, "", ""), (0, "advanced", "")])
        rec = dl.demo_operator_gate("feat/x", 7, "jam", run=run)
        self.assertTrue(rec.gate_refused)
        self.assertTrue(rec.gate_approved)
        self.assertTrue(rec.ok)
        self.assertEqual(rec.marker, "OPERATOR-SIMULATED")

    def test_demo_gate_fails_if_not_refused(self):
        # If advance-without-approval SUCCEEDS, the gate has no teeth → not ok.
        run = _fake_run([(0, "advanced (BUG)", ""), (0, "", ""), (0, "", "")])
        rec = dl.demo_operator_gate("feat/x", 7, "jam", run=run)
        self.assertFalse(rec.gate_refused)
        self.assertFalse(rec.ok)


class TestCheck(unittest.TestCase):
    def test_check_passes_on_complete_state(self):
        ok, results = dl.check(_complete_state())
        self.assertTrue(ok, msg=str(results))

    def test_check_fails_missing_substep(self):
        state = _complete_state()
        state["records"] = [r for r in state["records"] if r["substep"] != "retrospective"]
        ok, _ = dl.check(state)
        self.assertFalse(ok)

    def test_check_fails_on_rereview_misfire(self):
        state = _complete_state()
        state["rereview_misfire"] = True
        ok, _ = dl.check(state)
        self.assertFalse(ok)

    def test_check_fails_if_dummy_pr_not_merged(self):
        state = _complete_state()
        state["dummy_pr_state"] = "OPEN"
        ok, _ = dl.check(state)
        self.assertFalse(ok)

    def test_check_fails_if_gate_not_demoed(self):
        state = _complete_state()
        for r in state["records"]:
            if r["substep"] == "jam":
                r["gate_refused"] = False
        ok, _ = dl.check(state)
        self.assertFalse(ok)


class TestFixtures(unittest.TestCase):
    def test_full_plan_fixture_passes_readiness(self):
        from check_plan_readiness import score_plan
        results = score_plan(dl.FULL_PLAN)
        passed = sum(1 for _, p, _ in results if p)
        self.assertGreaterEqual(passed, 9, msg=str(results))

    def test_thin_plan_fixture_fails_readiness(self):
        from check_plan_readiness import score_plan
        results = score_plan(dl.THIN_PLAN)
        passed = sum(1 for _, p, _ in results if p)
        self.assertLess(passed, 9)

    def test_pr_body_has_six_sections(self):
        for n in range(1, 7):
            self.assertIn(f"## {n}.", dl.PR_BODY)


if __name__ == "__main__":
    unittest.main()
