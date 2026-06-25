"""Tests for skills/user_model/guardrail.py and guardrail wiring in store.py."""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from skills.user_model.guardrail import score_candidate, passes, DEFAULT_THRESHOLD


class TestScoreCandidate(unittest.TestCase):
    # ── Passing cases ────────────────────────────────────────────────────
    def test_clear_bhaga_principle_passes(self):
        text = (
            "For BHAGA: always run read-only diagnosis before asking the operator "
            "which date to fix — the date can be derived from Firestore."
        )
        result = score_candidate(text)
        self.assertGreaterEqual(result.score, DEFAULT_THRESHOLD, result.summary())

    def test_global_principle_passes(self):
        text = (
            "Always prefer sandbox e2e verification over touching production "
            "when proving a code change pre-merge."
        )
        result = score_candidate(text)
        self.assertGreaterEqual(result.score, DEFAULT_THRESHOLD, result.summary())

    def test_style_principle_passes(self):
        text = (
            "When asked for a binary decision, prefer giving a general direction "
            "and letting the agent choose the finer implementation detail."
        )
        result = score_candidate(text)
        self.assertGreaterEqual(result.score, DEFAULT_THRESHOLD, result.summary())

    # ── Failing cases ────────────────────────────────────────────────────
    def test_pr_number_fails_generalizable(self):
        text = "For PR #63, always run verify --full before pushing."
        result = score_candidate(text)
        failures = result.failures()
        self.assertIn("generalizable", failures, result.summary())

    def test_iso_date_fails_generalizable(self):
        text = "Re-run the 2026-06-23 nightly after the merge."
        result = score_candidate(text)
        self.assertIn("generalizable", result.failures(), result.summary())

    def test_numbered_procedure_fails_prescriptive(self):
        text = textwrap.dedent("""\
            Step 1: run git pull.
            Step 2: run pytest.
            Step 3: push.
            For BHAGA nightly, always use this sequence.
        """)
        result = score_candidate(text)
        self.assertIn("not_prescriptive", result.failures(), result.summary())

    def test_transient_marker_fails_durable(self):
        text = (
            "For now, always skip ADP for BHAGA until we fix the dialog issue."
        )
        result = score_candidate(text)
        self.assertIn("durable", result.failures(), result.summary())

    def test_no_action_verb_fails_actionable(self):
        text = "Sandbox tests are better than prod tests globally."
        result = score_candidate(text)
        self.assertIn("actionable", result.failures(), result.summary())

    # ── passes() helper ─────────────────────────────────────────────────
    def test_passes_returns_bool(self):
        good = "For BHAGA: always prefer sandbox runs over prod when verifying changes."
        bad = "Re-run PR #63 fixes today."
        self.assertTrue(passes(good))
        self.assertFalse(passes(bad))


class TestStoreGuardrailWiring(unittest.TestCase):
    """add_preference() must reject principle/style rows that fail the guardrail."""

    def test_add_rejects_low_score_principle(self):
        from skills.user_model.store import add_preference
        low_score_text = "Re-run the 2026-06-23 nightly after the merge."
        status, result = add_preference(
            "principle",
            {"#": "TEST", "Principle": low_score_text, "Source": "test"},
        )
        self.assertEqual(status, "rejected", f"Expected rejection, got {status}: {result}")

    def test_add_accepts_high_score_principle(self):
        """A valid principle lands in the preferences file."""
        from skills.user_model.store import add_preference, list_preferences
        good_text = (
            "For BHAGA tests: always prefer the existing full-live scenario "
            "over adding new scenarios when it already exercises the changed path."
        )
        # Run against the real store; dedup prevents doubles.
        status, _ = add_preference(
            "principle",
            {"#": "TGOOD", "Principle": good_text, "Source": "test"},
        )
        self.assertIn(status, ("added", "duplicate"),
                      f"Expected added or duplicate, got {status}")

    def test_domain_rows_bypass_guardrail(self):
        """Domain rows (facts, not rules) always bypass the guardrail."""
        from skills.user_model.store import add_preference
        status, _ = add_preference(
            "domain",
            {"Topic": "OS", "Detail": "macOS (Apple Silicon)", "Source": "test"},
        )
        self.assertIn(status, ("added", "duplicate"))

    def test_skip_guardrail_flag(self):
        """skip_guardrail=True bypasses the gate even for bad principle text."""
        from skills.user_model.store import add_preference
        low_score_text = "Re-run the 2026-06-23 nightly after the merge."
        status, _ = add_preference(
            "principle",
            {"#": "TSG", "Principle": low_score_text, "Source": "test"},
            skip_guardrail=True,
        )
        self.assertIn(status, ("added", "duplicate"))


class TestBackfillIdempotency(unittest.TestCase):
    def test_backfill_is_idempotent(self):
        """Running backfill twice should not add any rows the second time."""
        from skills.user_model.backfill import run
        # First run: all candidates either add or find duplicates; none rejected.
        r1 = run(dry_run=False)
        self.assertEqual(r1.get("rejected", 0), 0, f"First run had rejections: {r1}")
        # Second run: everything is already present -> all duplicates, nothing added.
        r2 = run(dry_run=False)
        self.assertEqual(r2.get("added", 0), 0, f"Expected 0 adds on second run: {r2}")
        self.assertEqual(r2.get("rejected", 0), 0)


if __name__ == "__main__":
    unittest.main()
