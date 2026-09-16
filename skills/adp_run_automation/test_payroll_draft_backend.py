"""Tests for ADP payroll draft guards (Issue #251)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from skills.adp_run_automation.payroll_draft_backend import (
    _aggregate_grid_rows,
    abort_if_forbidden_label,
    combine_preview_totals,
    header_index,
    hours_guardrail_failures,
    packet_from_view_rows,
    rate2_split,
    run_draft,
    solo_premium_keying_lines,
    solo_rate2_enabled,
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


class TestSoloPremiumHours(unittest.TestCase):
    """#309: which regular hours move to the $16.25 rate."""

    def _row(self, **over) -> dict:
        row = {
            "employee": "Willingham, Brooke",
            "labor_type": "Part-time",
            "hours_worked": 34.75,
            "ot_hours": 0,
            "wage_rate_dollars": 15.25,
            "review_bonus": 0,
            "recognition_bonus": 0,
            "perks": 0,
        }
        row.update(over)
        return row

    def test_eligible_employee_carries_solo_hours(self):
        pkt = packet_from_view_rows([self._row(solo_hours=4.75, solo_eligible=True)])
        self.assertEqual(pkt[0].solo_premium_hours, 4.75)

    def test_ineligible_employee_gets_no_premium_hours(self):
        """Someone already at $16.25 accrues solo hours but no premium."""
        pkt = packet_from_view_rows([
            self._row(wage_rate_dollars=16.25, solo_hours=6.0, solo_eligible=False)
        ])
        self.assertEqual(pkt[0].solo_premium_hours, 0.0)

    def test_absent_solo_data_defaults_to_zero(self):
        """No solo view (or a name that did not match) must not block a draft."""
        pkt = packet_from_view_rows([self._row()])
        self.assertEqual(pkt[0].solo_premium_hours, 0.0)

    def test_premium_hours_never_exceed_regular_hours(self):
        pkt = packet_from_view_rows([
            self._row(hours_worked=3.0, solo_hours=4.75, solo_eligible=True)
        ])
        self.assertEqual(pkt[0].solo_premium_hours, 3.0)

    def test_premium_hours_exclude_overtime(self):
        """OT is split out of regular, so the clamp is against regular alone."""
        pkt = packet_from_view_rows([
            self._row(hours_worked=42.0, ot_hours=2.0, solo_hours=41.0,
                      solo_eligible=True)
        ])
        self.assertEqual(pkt[0].regular_hours, 40.0)
        self.assertEqual(pkt[0].solo_premium_hours, 40.0)

    def test_keying_lines_split_base_and_premium_hours(self):
        pkt = packet_from_view_rows([self._row(solo_hours=4.75, solo_eligible=True)])
        self.assertEqual(
            solo_premium_keying_lines(pkt),
            ["Willingham, Brooke: rate-1 30.0h, rate-2 4.75h"],
        )

    def test_keying_lines_skip_employees_with_no_premium(self):
        pkt = packet_from_view_rows([
            self._row(solo_hours=0, solo_eligible=True),
            self._row(employee="Flores, Juan", solo_hours=2.5, solo_eligible=True),
        ])
        self.assertEqual(len(solo_premium_keying_lines(pkt)), 1)


def grid_row(employee: str, *, row_index: str, rate: float, reg: float = 0.0,
             pers: float = 0.0, hol: float = 0.0, ot: float = 0.0) -> dict:
    return {
        "employee": employee, "row_index": row_index, "rate": rate,
        "reg": reg, "pers": pers, "hol": hol, "ot": ot,
    }


class TestRate2Split(unittest.TestCase):
    """#309: splitting ADP's Regular hours across the base and premium rates."""

    def test_the_two_legs_always_resum_to_adps_regular_hours(self):
        keep, premium = rate2_split(adp_regular_hours=34.75, solo_hours=4.75)
        self.assertEqual((keep, premium), (30.0, 4.75))
        self.assertAlmostEqual(keep + premium, 34.75, places=2)

    def test_no_solo_hours_leaves_regular_untouched(self):
        self.assertEqual(rate2_split(adp_regular_hours=20.0, solo_hours=0), (20.0, 0.0))

    def test_solo_above_adps_regular_is_clamped_not_added(self):
        # Punches moved after the model ran. Keying 36h of premium against 30h of
        # Regular would inflate total paid hours; the premium absorbs all 30.
        keep, premium = rate2_split(adp_regular_hours=30.0, solo_hours=36.0)
        self.assertEqual((keep, premium), (0.0, 30.0))
        self.assertAlmostEqual(keep + premium, 30.0, places=2)

    def test_zero_regular_never_produces_a_premium_line(self):
        self.assertEqual(rate2_split(adp_regular_hours=0, solo_hours=5), (0.0, 0.0))

    def test_negative_inputs_are_floored(self):
        self.assertEqual(rate2_split(adp_regular_hours=-4, solo_hours=-2), (0.0, 0.0))


class TestSoloRate2Flag(unittest.TestCase):
    """The split rewrites live payroll hours, so it must be opt-in."""

    def test_off_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(solo_rate2_enabled())

    def test_on_only_for_truthy_values(self):
        for val, want in (
            ("1", True), ("true", True), ("YES", True),
            ("0", False), ("", False), ("no", False),
        ):
            with patch.dict("os.environ", {"BHAGA_ADP_SOLO_RATE2": val}, clear=True):
                self.assertIs(solo_rate2_enabled(), want, val)


class TestApplySoloRate2(unittest.TestCase):
    """#309: the live grid sequence that moves hours onto the premium rate."""

    MOD = "skills.adp_run_automation.payroll_draft_backend"
    NAME = "Willingham, Brooke"

    class _Page:
        """Enough of a Playwright page for the orchestrator: no next page."""

        def wait_for_timeout(self, _ms):
            return None

        def locator(self, _sel):
            class _L:
                first = None

                def __getattr__(self, _n):
                    raise RuntimeError("no pagination in this fixture")

            return _L()

    def _packet(self, solo=4.75):
        rows = [{
            "employee": self.NAME,
            "labor_type": "Part-time",
            "hours_worked": 34.75,
            "ot_hours": 0,
            "wage_rate_dollars": 15.25,
            "tips_allocated": 0,
            "review_bonus": 0,
            "recognition_bonus": 0,
            "perks": 0,
            "solo_hours": solo,
            "solo_eligible": True,
        }]
        with patch(f"{self.MOD}._merge_solo_hours", side_effect=lambda r, _p: r):
            return packet_from_view_rows(rows)

    def _run(self, grid_states, **over):
        """Run the orchestrator against a scripted sequence of grid reads."""
        calls: list[tuple] = []
        states = list(grid_states)

        def _grid(_page):
            return states.pop(0) if len(states) > 1 else states[0]

        def _fill(page, *, employee, col_id, amount, row_index=None):
            calls.append(("fill", row_index, amount))
            return True

        patches = {
            "_ag_enter_page_hours": _grid,
            "_open_row_action_menu": lambda p, *, row_index: calls.append(
                ("menu", row_index)
            ) is None,
            "_click_menu_item": lambda p, label: calls.append(("item", label)) is None,
            "_new_row_index_for": lambda p, *, employee, exclude: "5",
            "_select_available_rate": lambda p, *, row_index, rate_dollars: calls.append(
                ("rate", row_index, rate_dollars)
            ) is None,
            "_fill_grid_amount": _fill,
            **over,
        }
        with patch.multiple(self.MOD, **{k: v for k, v in patches.items()}):
            from skills.adp_run_automation.payroll_draft_backend import (
                _apply_solo_rate2,
            )

            out = _apply_solo_rate2(
                self._Page(), self._packet(), premium_rate=16.25
            )
        return out, calls

    def _one_row_grid(self, reg=34.75):
        return {self.NAME: {
            "reg": reg, "hours": reg, "ot": 0.0, "rate": 15.25,
            "rows": [{"row_index": "4", "reg": reg, "ot": 0.0}],
        }}

    def test_premium_row_is_filled_before_the_base_row_is_reduced(self):
        # Order matters on a crash: hours too high trips the guardrail, whereas
        # reducing first would leave a short paycheck that looks self-consistent.
        out, calls = self._run([self._one_row_grid(), self._one_row_grid()])
        fills = [c for c in calls if c[0] == "fill"]
        self.assertEqual(fills, [("fill", "5", 4.75), ("fill", "4", 30.0)])
        self.assertEqual(out["applied"], [self.NAME])
        self.assertEqual(out["failed"], [])

    def test_adds_the_row_then_picks_the_premium_rate_on_it(self):
        _out, calls = self._run([self._one_row_grid(), self._one_row_grid()])
        self.assertEqual(calls[0], ("menu", "4"))
        self.assertEqual(calls[1], ("item", "Add row"))
        self.assertEqual(calls[2], ("rate", "5", 16.25))

    def test_an_already_split_employee_is_skipped_not_split_again(self):
        # Rerunning against the same draft must not pay the uplift twice.
        already = {self.NAME: {
            "reg": 34.75, "hours": 34.75, "ot": 0.0, "rate": 15.25,
            "rows": [
                {"row_index": "4", "reg": 30.0, "ot": 0.0},
                {"row_index": "5", "reg": 4.75, "ot": 0.0},
            ],
        }}
        out, calls = self._run([already])
        self.assertEqual(out["failed"], [])
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_a_changed_total_is_reported_as_a_failure(self):
        # Verification re-reads the grid; 34.75 -> 39.50 means the base row was
        # never reduced, so the employee would be overpaid.
        broken = {self.NAME: {
            "reg": 39.50, "hours": 39.50, "ot": 0.0, "rate": 15.25,
            "rows": [{"row_index": "4", "reg": 39.50, "ot": 0.0}],
        }}
        out, _calls = self._run([self._one_row_grid(), broken])
        self.assertEqual(out["applied"], [])
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("total_changed", out["failed"][0])

    def test_a_missing_add_row_item_fails_without_touching_hours(self):
        out, calls = self._run(
            [self._one_row_grid(), self._one_row_grid()],
            _click_menu_item=lambda p, label: False,
        )
        self.assertEqual(out["applied"], [])
        self.assertIn("no_add_row", out["failed"][0])
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_nobody_eligible_is_a_no_op(self):
        with patch(f"{self.MOD}._merge_solo_hours", side_effect=lambda r, _p: r):
            from skills.adp_run_automation.payroll_draft_backend import (
                _apply_solo_rate2,
            )

            out = _apply_solo_rate2(self._Page(), [], premium_rate=16.25)
        self.assertEqual(out, {"applied": [], "failed": [], "premium_hours": 0.0})


class TestTwoRateGridRows(unittest.TestCase):
    """Solo-shift pay (#309) puts one employee on two Enter-payroll rows."""

    NAME = "Willingham, Brooke"

    def test_hours_sum_across_an_employees_line_items(self):
        agg = _aggregate_grid_rows([
            grid_row(self.NAME, row_index="4", rate=15.25, reg=30.0),
            grid_row(self.NAME, row_index="5", rate=16.25, reg=4.75),
        ])
        self.assertEqual(agg[self.NAME]["hours"], 34.75)
        self.assertEqual(agg[self.NAME]["reg"], 34.75)

    def test_reported_rate_stays_the_base_not_the_premium(self):
        agg = _aggregate_grid_rows([
            grid_row(self.NAME, row_index="5", rate=16.25, reg=4.75),
            grid_row(self.NAME, row_index="4", rate=15.25, reg=30.0),
        ])
        self.assertEqual(agg[self.NAME]["rate"], 15.25)

    def test_every_line_item_is_addressable_by_row_index(self):
        """The zeroing pass needs each row, or a stale premium row still pays."""
        agg = _aggregate_grid_rows([
            grid_row(self.NAME, row_index="4", rate=15.25, reg=30.0),
            grid_row(self.NAME, row_index="5", rate=16.25, reg=4.75),
        ])
        self.assertEqual(
            [r["row_index"] for r in agg[self.NAME]["rows"]], ["4", "5"]
        )

    def test_ot_and_other_earnings_also_sum(self):
        agg = _aggregate_grid_rows([
            grid_row(self.NAME, row_index="4", rate=15.25, reg=38.0, hol=8.0),
            grid_row(self.NAME, row_index="5", rate=16.25, reg=4.0, ot=2.0),
        ])
        rec = agg[self.NAME]
        self.assertEqual(rec["reg"], 42.0)
        self.assertEqual(rec["hol"], 8.0)
        self.assertEqual(rec["ot"], 2.0)
        self.assertEqual(rec["hours"], 52.0)

    def test_single_rate_employees_are_unchanged(self):
        agg = _aggregate_grid_rows([
            grid_row("Flores, Juan", row_index="1", rate=15.25, reg=20.0),
            grid_row("Garcia, Jacob", row_index="2", rate=15.25, reg=18.5),
        ])
        self.assertEqual(agg["Flores, Juan"]["hours"], 20.0)
        self.assertEqual(agg["Garcia, Jacob"]["hours"], 18.5)
        self.assertEqual(len(agg["Flores, Juan"]["rows"]), 1)

    def test_rows_without_a_name_are_dropped(self):
        agg = _aggregate_grid_rows([grid_row("", row_index="9", rate=0.0, reg=5.0)])
        self.assertEqual(agg, {})


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
