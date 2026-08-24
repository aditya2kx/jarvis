"""Tests for ADP payroll draft guards (Issue #251)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from skills.adp_run_automation.payroll_draft_backend import (
    abort_if_forbidden_label,
    combine_preview_totals,
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

    def test_keep_draft_dry_run_ok(self):
        out = run_draft(
            store="palmetto",
            period_start="2026-08-10",
            period_end="2026-08-17",
            keep_draft=True,
            view_rows=[],
        )
        self.assertTrue(out["dry_run"])
        self.assertFalse(out["started"])

    def test_live_preview_runs_once(self):
        live = {
            "started": True,
            "deleted": False,
            "guardrail_fails": [],
            "screenshots": [],
            "preview_url": "https://runpayrollmain.adp.com/preview",
            "preview_hours": 458.97,
            "preview_gross": 8999.06,
        }
        with (
            patch(
                "skills.adp_run_automation.payroll_draft_backend.run_live_preview",
                return_value=live,
            ) as preview,
            patch(
                "skills.adp_run_automation.payroll_draft_backend.record_payroll_draft_run",
            ) as record,
        ):
            out = run_draft(
                store="palmetto",
                period_start="2026-08-10",
                period_end="2026-08-23",
                dry_run=False,
                allow_prod_draft=True,
                allow_start=True,
                view_rows=[{"employee": "A", "hours_worked": 8, "ot_hours": 0}],
            )
        self.assertEqual(preview.call_count, 1)
        self.assertEqual(
            [c.kwargs["status"] for c in record.call_args_list],
            ["running", "ok"],
        )
        self.assertTrue(out["started"])
        self.assertEqual(out["preview_url"], live["preview_url"])
        self.assertEqual(out["preview_hours"], 458.97)
        self.assertEqual(out["preview_gross"], 8999.06)


class TestPreviewTotals(unittest.TestCase):
    def test_footer_beats_row_sum(self):
        hours, gross = combine_preview_totals(
            {"A, B": {"hours": 10, "gross": 100}},
            {"hours": 458.97, "gross": 8999.06},
        )
        self.assertEqual(hours, 458.97)
        self.assertEqual(gross, 8999.06)

    def test_row_sum_when_no_footer(self):
        hours, gross = combine_preview_totals(
            {
                "A, B": {"hours": 10.5, "gross": 200.1},
                "C, D": {"hours": 2.5, "gross": 50.4},
            },
            {},
        )
        self.assertEqual(hours, 13.0)
        self.assertEqual(gross, 250.5)


class TestHeadlessPreviewUrl(unittest.TestCase):
    def test_operator_url_prefers_live_page(self):
        from skills.adp_run_automation.payroll_draft_backend import (
            operator_adp_preview_url,
        )

        live = (
            "https://runpayrollmain.adp.com/@836d254c-789b-41b8-8052-d48a639e95d8"
            "/v2/payroll/preview"
        )
        home = (
            "https://runpayrollmain.adp.com/@836d254c-789b-41b8-8052-d48a639e95d8/v2/"
        )
        self.assertEqual(operator_adp_preview_url(live, home), live)
        self.assertEqual(operator_adp_preview_url("about:blank", home), home)
        self.assertTrue(
            operator_adp_preview_url("", "").startswith("https://runpayroll.adp.com")
        )

    def test_headed_only_when_env_set(self):
        import os

        from skills.adp_run_automation.payroll_draft_backend import _adp_headed

        prev = os.environ.get("BHAGA_ADP_HEADED")
        try:
            os.environ.pop("BHAGA_ADP_HEADED", None)
            self.assertFalse(_adp_headed())
            os.environ["BHAGA_ADP_HEADED"] = "1"
            self.assertTrue(_adp_headed())
        finally:
            if prev is None:
                os.environ.pop("BHAGA_ADP_HEADED", None)
            else:
                os.environ["BHAGA_ADP_HEADED"] = prev


if __name__ == "__main__":
    unittest.main()
