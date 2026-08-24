"""Tests for ADP payroll draft guards (Issue #251)."""

from __future__ import annotations

import unittest

from skills.adp_run_automation.payroll_draft_backend import (
    abort_if_forbidden_label,
    header_index,
    hours_guardrail_failures,
    packet_from_view_rows,
    run_draft,
    wage_guardrail_failures,
)


class TestApproveDenylist(unittest.TestCase):
    def test_approve_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            abort_if_forbidden_label("Approve payroll")
        self.assertIn("forbid_click", str(ctx.exception))

    def test_hours_ok(self):
        abort_if_forbidden_label("Hours")
        abort_if_forbidden_label("Run payroll")
        abort_if_forbidden_label("Don't save")

    def test_save_and_finish_later_raise(self):
        with self.assertRaises(RuntimeError):
            abort_if_forbidden_label("Finish Later")
        with self.assertRaises(RuntimeError):
            abort_if_forbidden_label("Save")
        abort_if_forbidden_label("Save and continue")


class TestPacketAndDryRun(unittest.TestCase):
    def test_packet_splits_ot_and_bonus(self):
        rows = [
            {
                "employee": "Krause, Lindsay",
                "labor_type": "Full-time",
                "hours_worked": 41,
                "ot_hours": 1,
                "wage_rate_dollars": 25,
                "review_bonus": 0,
                "recognition_bonus": 0,
                "perks": 20,
            }
        ]
        pkt = packet_from_view_rows(rows)
        self.assertEqual(pkt[0].regular_hours, 40)
        self.assertEqual(pkt[0].ot_hours, 1)
        self.assertEqual(pkt[0].misc_reimbursement_dollars, 20)
        self.assertEqual(pkt[0].est_wages_dollars, 40 * 25 + 1 * 37.5)

    def test_est_wages_half_up_matches_adp(self):
        from skills.adp_run_automation.payroll_draft_backend import est_wages_dollars

        self.assertEqual(
            est_wages_dollars(
                regular_hours=47.30,
                ot_hours=0,
                wage_rate=15.25,
            ),
            721.33,
        )
        self.assertEqual(
            est_wages_dollars(
                regular_hours=43.98,
                ot_hours=0,
                wage_rate=16.25,
            ),
            714.68,
        )
        self.assertEqual(round(47.30 * 15.25, 2), 721.32)


class TestGuardrails(unittest.TestCase):
    def test_hours_within_30_min_pass(self):
        self.assertEqual(
            hours_guardrail_failures({"A, B": 10.0}, {"A, B": 10.4}),
            [],
        )

    def test_hours_over_30_min_fail(self):
        fails = hours_guardrail_failures({"A, B": 10.0}, {"A, B": 10.6})
        self.assertTrue(any("hours_mismatch" in f for f in fails))

    def test_name_key_strips_rate_suffix(self):
        from skills.adp_run_automation.payroll_draft_backend import name_key

        self.assertEqual(
            name_key("Alvarez, Sebastian $15.2500 / hr"),
            name_key("Alvarez, Sebastian"),
        )
        self.assertEqual(
            hours_guardrail_failures(
                {"Alvarez, Sebastian": 23.28},
                {"Alvarez, Sebastian $15.2500 / hr": 23.28},
            ),
            [],
        )
        fails = hours_guardrail_failures({"A, B": 8.0}, {})
        self.assertTrue(any("hours_missing_on_adp" in f for f in fails))

    def test_hours_extra_on_adp_fail(self):
        fails = hours_guardrail_failures(
            {"Krause, Lindsay": 80.0},
            {"Krause, Lindsay": 80.0, "Flores, Juan": 30.0},
        )
        self.assertTrue(any("hours_extra_on_adp" in f for f in fails))
        self.assertEqual(
            hours_guardrail_failures(
                {"Krause, Lindsay": 80.0, "Flores, Juan": 0.0},
                {"Krause, Lindsay": 80.0, "Flores, Juan": 0.0},
            ),
            [],
        )

    def test_missing_punch_flag(self):
        fails = hours_guardrail_failures(
            {}, {}, missing_punch_names=["A, B"]
        )
        self.assertEqual(fails, ["missing_punch A, B"])

    def test_wages_tolerance(self):
        self.assertEqual(
            wage_guardrail_failures({"A": 100.0}, {"A": 100.99}),
            [],
        )
        fails = wage_guardrail_failures({"A": 100.0}, {"A": 101.01})
        self.assertTrue(any("wages_mismatch" in f for f in fails))

    def test_header_index_tips_misc_bonus(self):
        headers = [
            "Name",
            "Rate",
            "Regular Hours",
            "Misc Reimb",
            "NQCCTips Owed (Nonqualified)",
            "Bonus",
        ]
        self.assertEqual(header_index(headers, ("nqcc", "tips owed")), 4)
        self.assertEqual(header_index(headers, ("misc reimb",)), 3)
        self.assertEqual(header_index(headers, ("bonus",)), 5)
        self.assertIsNone(header_index(headers, ("nope",)))

    def test_dry_run_does_not_start(self):
        out = run_draft(
            store="palmetto",
            period_start="2026-08-10",
            period_end="2026-08-17",
            dry_run=True,
            view_rows=[{"employee": "A", "hours_worked": 8, "ot_hours": 0}],
        )
        self.assertTrue(out["dry_run"])
        self.assertFalse(out["started"])
        self.assertEqual(out["packet"][0]["employee"], "A")

    def test_live_without_allow_refuses(self):
        with self.assertRaises(RuntimeError) as ctx:
            run_draft(
                store="palmetto",
                period_start="2026-08-10",
                period_end="2026-08-17",
                dry_run=False,
                allow_prod_draft=False,
                view_rows=[],
            )
        self.assertIn("refused_start", str(ctx.exception))

    def test_keep_draft_forbidden(self):
        with self.assertRaises(RuntimeError) as ctx:
            run_draft(
                store="palmetto",
                period_start="2026-08-10",
                period_end="2026-08-17",
                keep_draft=True,
                view_rows=[],
            )
        self.assertIn("keep_draft_forbidden", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
