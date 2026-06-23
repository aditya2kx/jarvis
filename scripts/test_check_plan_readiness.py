#!/usr/bin/env python3
"""Tests for check_plan_readiness.py."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_plan_readiness as cpr


# ---------------------------------------------------------------------------
# Known-good plan text fixture (covers all 10 items)
# ---------------------------------------------------------------------------

GOOD_PLAN = """\
---
name: test-plan
---
# Test Plan

## Why
We need to test things properly.

## Milestone 1 — Setup
Edit `scripts/foo.py` line 42 to add the function.

```python
def my_func(x: int) -> str:
    \"\"\"Stub.\"\"\"
    return str(x)
```

**Verify (copy-paste):**
```bash
python3 -m pytest scripts/test_foo.py -v
```

## Milestone 2 — Implementation
Edit `agents/bhaga/scripts/update_model_sheet.py:128` to call the new function.

```bash
python3 scripts/verify.py --full
```

**Verify (copy-paste):**
```bash
python3 -m pytest agents/bhaga/scripts/ -v
```

## Milestone 3 — Evidence
Assemble the PR §4 evidence pack:
- Happy path: python3 scripts/new_requirement.py --dry-run "demo"
- Failure recovery: verify.py --full shows FAIL then PASS after fix

**Verify (copy-paste):**
```bash
python3 scripts/check_plan_readiness.py --plan this.plan.md
```

## Scope
- **In scope:** foo.py changes, bhaga script update, PR mechanics
- **Out of scope:** RUNBOOK.md rewrite, CI workflow changes
- Feature-flag decision: no flag needed — additive change, no wrong numbers risk

## Invariants preserved
- Idempotent upserts maintained
- No hardcoding (multi-store config-driven)
- Forward-only phase state

## Docs lock-step
- RUNBOOK.md: update after bhaga script changes
- PROGRESS.md: dated entry at end
- check_doc_freshness.py COUPLINGS: add new coupling

## PR mechanics
- Branch: feat/test; never self-merge; bot account (jarvis-agent-bot328)
- `gh pr create`; babysit to green; pr-workflow.mdc governs

## Model routing
- Milestone 1: Sonnet 4.6 (logic)
- Milestone 2: Sonnet 4.6 (logic)
- Milestone 3: Composer (doc edits)

## Evidence
- Per-scenario evidence: happy path + failure recovery + legacy
- Sandbox tier: Tier-1 e2e; Tier-2 live run not needed (no live-only paths)
"""

# A minimal stub plan that should fail most checklist items
STUB_PLAN = """\
# Stub Plan

## Task
Fix the bug.

## Steps
1. Open the file.
2. Change the value.
3. Push.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScorePlan(unittest.TestCase):
    def test_good_plan_passes_all_items(self):
        results = cpr.score_plan(GOOD_PLAN)
        failed = [(label, detail) for label, passed, detail in results if not passed]
        self.assertEqual(
            failed, [],
            f"Good plan should pass all items; failed: {failed}"
        )

    def test_stub_plan_fails_most_items(self):
        results = cpr.score_plan(STUB_PLAN)
        passed_count = sum(1 for _, passed, _ in results if passed)
        self.assertLess(
            passed_count, 5,
            f"Stub plan should fail most items; passed {passed_count}/10"
        )

    def test_stub_plan_fails_evidence(self):
        results = cpr.score_plan(STUB_PLAN)
        item4 = next(r for r in results if "evidence" in r[0].lower())
        self.assertFalse(item4[1], "Stub plan should fail evidence check")

    def test_stub_plan_fails_line_citations(self):
        results = cpr.score_plan(STUB_PLAN)
        item1 = next(r for r in results if "citation" in r[0].lower())
        self.assertFalse(item1[1], "Stub plan should fail file:line citation check")

    def test_stub_plan_fails_milestones(self):
        results = cpr.score_plan(STUB_PLAN)
        item3 = next(r for r in results if "milestone" in r[0].lower())
        self.assertFalse(item3[1], "Stub plan should fail milestone check")

    def test_stub_plan_fails_model_routing(self):
        results = cpr.score_plan(STUB_PLAN)
        item10 = next(r for r in results if "model" in r[0].lower())
        self.assertFalse(item10[1], "Stub plan should fail model routing check")


class TestIndividualChecks(unittest.TestCase):
    def test_file_line_citation_direct(self):
        text = "Edit `scripts/foo.py:42` to add the function stub."
        passed, detail = cpr._check_file_line_citations(text)
        self.assertTrue(passed, f"Should pass: {detail}")

    def test_file_line_citation_fails_on_plain_text(self):
        text = "Change the value in the main file."
        passed, _ = cpr._check_file_line_citations(text)
        self.assertFalse(passed, "Should fail: no file:line reference")

    def test_inline_stubs_code_fence_with_bash(self):
        text = "```bash\npython3 scripts/verify.py --full\n```"
        passed, _ = cpr._check_inline_stubs(text)
        self.assertTrue(passed)

    def test_inline_stubs_fails_no_fence(self):
        text = "Run the command manually."
        passed, _ = cpr._check_inline_stubs(text)
        self.assertFalse(passed)

    def test_milestones_three_present(self):
        text = (
            "## Milestone 1 — A\n**Verify:**\n```bash\npytest\n```\n"
            "## Milestone 2 — B\n**Verify:**\n```bash\npytest\n```\n"
            "## Milestone 3 — C\n**Verify:**\n```bash\npython3 scripts/verify.py\n```\n"
        )
        passed, _ = cpr._check_milestones_with_verify(text)
        self.assertTrue(passed)

    def test_milestones_only_two_fails(self):
        text = (
            "## Milestone 1 — A\n**Verify:**\n```bash\npytest\n```\n"
            "## Milestone 2 — B\n**Verify:**\n```bash\npytest\n```\n"
        )
        passed, _ = cpr._check_milestones_with_verify(text)
        self.assertFalse(passed)

    def test_docs_lockstep_with_runbook(self):
        text = "Update RUNBOOK.md and check_doc_freshness.py after this change."
        passed, _ = cpr._check_docs_lockstep(text)
        self.assertTrue(passed)

    def test_branch_pr_mechanics_with_signals(self):
        text = "Never self-merge. Use `gh pr create` and babysit to green."
        passed, _ = cpr._check_branch_pr_mechanics(text)
        self.assertTrue(passed)

    def test_model_routing_with_sonnet(self):
        text = "Model routing: Milestone 1: Sonnet 4.6 medium."
        passed, _ = cpr._check_model_routing(text)
        self.assertTrue(passed)

    def test_per_scenario_evidence_with_happy_path(self):
        text = "Evidence: happy path passes. Failure recovery: verify.py --full. PR §4 evidence pack."
        passed, _ = cpr._check_per_scenario_evidence(text)
        self.assertTrue(passed)


class TestScoreFunction(unittest.TestCase):
    def test_returns_ten_items(self):
        results = cpr.score_plan(GOOD_PLAN)
        self.assertEqual(len(results), 10)

    def test_result_structure(self):
        results = cpr.score_plan(GOOD_PLAN)
        for item in results:
            label, passed, detail = item
            self.assertIsInstance(label, str)
            self.assertIsInstance(passed, bool)
            self.assertIsInstance(detail, str)


if __name__ == "__main__":
    unittest.main()
