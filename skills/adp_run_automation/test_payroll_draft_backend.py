"""Tests for ADP payroll draft guards (Issue #251)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from skills.adp_run_automation.payroll_draft_backend import (
    _aggregate_grid_rows,
    _attribute_grid_rows,
    _paginate_timecard_hours,
    abort_if_forbidden_label,
    classify_rate2_state,
    expected_gross_dollars,
    extra_rate_line_hours,
    solo_premium_uplift_dollars,
    combine_preview_totals,
    header_index,
    hours_guardrail_failures,
    packet_from_view_rows,
    rate2_split,
    unrepairable_hours_fails,
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


def pinned(row_index: str, name_text: str, *, reg: float = 0.0, rate: float = 15.25,
           has_body: bool = True) -> dict:
    return {
        "row_index": row_index, "name_text": name_text, "has_body": has_body,
        "rate": rate, "reg": reg, "pers": 0.0, "hol": 0.0, "ot": 0.0,
    }


class TestContinuationRowsAreAttributed(unittest.TestCase):
    """#309: ADP names an employee's first line item only.

    A second rate line comes back with an empty pinned-left cell. The reader used
    to skip nameless rows, so the row it had just added was invisible — and the
    "not the base index" fallback then picked the employee's own renumbered
    original row. Live 2026-09-21: the premium landed on the original row and the
    reduced base hours landed on a different employee's row.
    """

    def test_a_nameless_row_belongs_to_the_named_row_above_it(self):
        rows = _attribute_grid_rows([
            pinned("0", "Garcia, Jacob", reg=41.65),
            pinned("1", "", reg=11.48, rate=16.25),
            pinned("2", "Guerrero, Amy", reg=29.35),
        ])
        self.assertEqual(
            [(r["employee"], r["row_index"], r["reg"]) for r in rows],
            [
                ("Garcia, Jacob", "0", 41.65),
                ("Garcia, Jacob", "1", 11.48),
                ("Guerrero, Amy", "2", 29.35),
            ],
        )
        self.assertEqual([r["continuation"] for r in rows], [False, True, False])

    def test_the_added_row_is_visible_before_any_hours_are_typed(self):
        """Straight after "Add row" the new line is empty — still must be seen."""
        rows = _attribute_grid_rows([
            pinned("0", "Garcia, Jacob", reg=53.13),
            pinned("1", "", reg=0.0, rate=0.0),
        ])
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["continuation"])

    def test_rows_are_attributed_in_grid_order_not_dom_order(self):
        rows = _attribute_grid_rows([
            pinned("1", "", reg=11.48),
            pinned("0", "Garcia, Jacob", reg=41.65),
        ])
        self.assertEqual([r["employee"] for r in rows], ["Garcia, Jacob"] * 2)

    def test_filler_rows_before_the_first_name_are_dropped(self):
        rows = _attribute_grid_rows([
            pinned("0", "", reg=0.0, has_body=False),
            pinned("1", "Garcia, Jacob", reg=53.13),
        ])
        self.assertEqual([r["employee"] for r in rows], ["Garcia, Jacob"])

    def test_a_bodyless_nameless_row_is_not_charged_to_anyone(self):
        """A rendering artifact must not become a phantom pay line."""
        rows = _attribute_grid_rows([
            pinned("0", "Garcia, Jacob", reg=53.13),
            pinned("1", "", reg=0.0, has_body=False),
        ])
        self.assertEqual(len(rows), 1)

    def test_hours_still_aggregate_to_the_employees_total(self):
        rows = _attribute_grid_rows([
            pinned("0", "Garcia, Jacob", reg=41.65),
            pinned("1", "", reg=11.48, rate=16.25),
        ])
        agg = _aggregate_grid_rows(rows)
        self.assertAlmostEqual(agg["Garcia, Jacob"]["reg"], 53.13, places=2)
        self.assertEqual(agg["Garcia, Jacob"]["rate"], 15.25)


class FakePaginatedGrid:
    """An AG Grid whose ``row-index`` restarts at 0 on every page, as ADP's does.

    The pages are read through the real ``_paginate_timecard_hours`` walk, so the
    fake only has to serve rows and honour the next/prev chevrons.
    """

    def __init__(self, pages: list[list[dict]], *, advances: bool = True):
        self.pages = pages
        self.advances = advances
        self.i = 0

    # -- page object surface used by the walk ---------------------------------
    def locator(self, selector: str):
        forward = "chevron-right" in selector
        return FakeChevron(self, forward=forward)

    def wait_for_timeout(self, _ms):
        return None

    def rows(self) -> list[dict]:
        return self.pages[self.i]


class FakeChevron:
    def __init__(self, grid: FakePaginatedGrid, *, forward: bool):
        self.grid = grid
        self.forward = forward

    @property
    def first(self):
        return self

    def is_visible(self):
        return True

    def is_enabled(self):
        if self.forward:
            return self.grid.i < len(self.grid.pages) - 1
        return self.grid.i > 0

    def click(self):
        if not self.grid.advances:
            return  # simulates a chevron that looks live but does not page
        self.grid.i += 1 if self.forward else -1


class TestGridPaginationAcrossPages(unittest.TestCase):
    """#309 regression: row-index is page-relative, so it cannot key the walk.

    Live 2026-09-21: a 15-person roster at 10/page had page 2's four rows
    overwrite page 1's first four. The three of those with hours read as absent,
    the hours guardrail failed n=3, and the rate-2 split was skipped — while ADP
    itself was correct (all three reconciled exactly at Preview).
    """

    # Page 2's indices restart at 0 and collide with page 1's 0..3.
    PAGE_1 = [
        grid_row("Alvarez, Sebastian", row_index="0", rate=15.25, reg=9.2),
        grid_row("Browning, Skyler", row_index="1", rate=15.25, reg=0.0),
        grid_row("Garcia, Jacob", row_index="2", rate=15.25, reg=53.13),
        grid_row("Guerrero, Amy", row_index="3", rate=15.25, reg=29.35),
        grid_row("Huynh, Hillary", row_index="4", rate=15.25, reg=29.52),
    ]
    PAGE_2 = [
        grid_row("Pascone, Kayah A", row_index="0", rate=16.25, reg=0.0),
        grid_row("Perales, Elizabeth", row_index="1", rate=15.25, reg=25.2),
        grid_row("Priyosha, Jarin", row_index="2", rate=15.25, reg=18.83),
        grid_row("Willingham, Brooke", row_index="3", rate=15.25, reg=6.93),
    ]

    def _walk(self, pages, **kw):
        grid = FakePaginatedGrid(pages, **kw)
        with patch(
            "skills.adp_run_automation.payroll_draft_backend._ag_grid_rows",
            side_effect=lambda _p: grid.rows(),
        ):
            return _paginate_timecard_hours(grid)

    def test_no_employee_is_lost_to_a_row_index_collision(self):
        hours = self._walk([self.PAGE_1, self.PAGE_2])
        self.assertEqual(len(hours), 9)
        # The three that vanished live, with the hours ADP actually had.
        self.assertEqual(hours["Alvarez, Sebastian"], 9.2)
        self.assertEqual(hours["Garcia, Jacob"], 53.13)
        self.assertEqual(hours["Guerrero, Amy"], 29.35)
        # ...and page 2 is still read.
        self.assertEqual(hours["Willingham, Brooke"], 6.93)

    def test_the_walk_returns_to_the_first_page(self):
        grid = FakePaginatedGrid([self.PAGE_1, self.PAGE_2])
        with patch(
            "skills.adp_run_automation.payroll_draft_backend._ag_grid_rows",
            side_effect=lambda _p: grid.rows(),
        ):
            _paginate_timecard_hours(grid)
        self.assertEqual(grid.i, 0, "later passes address rows on page 1")

    def test_a_chevron_that_does_not_advance_cannot_double_count(self):
        """The page ordinal is only safe while the page really turns."""
        hours = self._walk([self.PAGE_1, self.PAGE_2], advances=False)
        self.assertEqual(hours["Garcia, Jacob"], 53.13)
        self.assertNotIn("Willingham, Brooke", hours)

    def test_a_single_page_roster_still_reads(self):
        hours = self._walk([self.PAGE_1])
        self.assertEqual(hours["Huynh, Hillary"], 29.52)
        self.assertEqual(len(hours), 5)

    def test_two_rate_line_items_still_sum_across_a_page_boundary(self):
        """The key must stay per-row, not per-employee: #309 pays on two lines."""
        split_p1 = self.PAGE_1 + [
            grid_row("Willingham, Brooke", row_index="5", rate=15.25, reg=2.18)
        ]
        hours = self._walk([split_p1, self.PAGE_2])
        self.assertAlmostEqual(hours["Willingham, Brooke"], 9.11, places=2)


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

        keyboard = type("_Keyboard", (), {"press": lambda self, key: None})()

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

    def _run(self, grid_states, after_rows=None, grew=1,
             line_count_after_add=2, fill_results=None, **over):
        """Run the orchestrator against a scripted sequence of grid reads.

        ``after_rows`` is what the post-split verification read returns; it
        defaults to a correct two-line split of the first state's hours.
        ``grew`` is how many rows the whole grid gained from "Add row", and
        ``line_count_after_add`` how many of them landed on this employee —
        the two differ precisely when the click hit the wrong row.
        """
        calls: list[tuple] = []
        states = list(grid_states)
        fill_results = list(fill_results or [])

        def _grid(_page):
            return states.pop(0) if len(states) > 1 else states[0]

        def _fill(page, *, employee, col_id, amount, row_index=None):
            calls.append(("fill", row_index, amount))
            return fill_results.pop(0) if fill_results else True

        if after_rows is None:
            # A correct split is two rows AND the second one on the premium rate.
            # Hours alone are not the property being verified.
            after_rows = [
                {"employee": self.NAME, "row_index": "4", "reg": 30.0, "ot": 0.0,
                 "rate": 15.25},
                {"employee": self.NAME, "row_index": "5", "reg": 4.75, "ot": 0.0,
                 "rate": 16.25},
            ]

        # Whole-grid row count: one row before the insert, plus `grew` after.
        reads = {"n": 0}

        def _all_rows(_page):
            reads["n"] += 1
            base = 10
            return [{}] * (base if reads["n"] == 1 else base + grew)

        def _item(p, label, *, test_id="", row_index=""):
            calls.append(("item", label, row_index))
            return True

        patches = {
            # The orchestrator reads every grid page, not the displayed one: an
            # employee's two rate lines can straddle a page boundary.
            "_paginate_timecard_detail": _grid,
            "_grid_goto_page": lambda p, n: True,
            "_ag_grid_rows": _all_rows,
            "_dismiss_adp_error_dialog": lambda _p: False,
            "_open_row_action_menu": lambda p, *, row_index: calls.append(
                ("menu", row_index)
            ) is None,
            "_click_menu_item": _item,
            "_rate2_row_indices": lambda p, *, employee, base_reg: ("4", "5"),
            "_employee_rows": lambda p, employee: list(after_rows),
            "_employee_rows_all_pages": lambda p, employee: list(after_rows),
            "_employee_line_count": lambda p, employee: line_count_after_add,
            # The orchestrator uses the *verified* picker: a pick that reports
            # success without the row landing on the premium pays base.
            "_select_rate_checked": lambda p, *, employee, row_index, rate_dollars: (
                calls.append(("rate", row_index, rate_dollars)) is None
            ),
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

    def test_adds_the_row_then_picks_the_premium_rate_on_it(self):
        _out, calls = self._run([self._one_row_grid(), self._one_row_grid()])
        self.assertEqual(calls[0], ("menu", "4"))
        self.assertEqual(calls[1], ("item", "Add row", "4"))
        self.assertEqual(calls[2], ("rate", "5", 16.25))

    def test_the_base_row_is_reduced_before_the_premium_is_added(self):
        """Order is a safety property: a stall must not leave hours inflated.

        Live 2026-09-21 the premium went in first, the base-row dblclick timed
        out behind the open rate dropdown, and Garcia was left at 53.13 + 11.48
        = 64.61 in a draft that was otherwise approvable.
        """
        _out, calls = self._run([self._one_row_grid(), self._one_row_grid()])
        fills = [c for c in calls if c[0] == "fill"]
        self.assertEqual(fills, [("fill", "4", 30.0), ("fill", "5", 4.75)])

    def test_a_failed_premium_fill_restores_the_base_row(self):
        """Never leave the base reduced *and* no premium: that underpays."""
        out, calls = self._run(
            [self._one_row_grid()],
            # base ok, then 3 premium attempts fail, then the restore.
            fill_results=[True, False, False, False, True],
        )
        self.assertEqual(out["applied"], [])
        self.assertIn("premium_fill_failed", out["failed"][0])
        self.assertIn("base_restored=True", out["failed"][0])
        self.assertEqual(calls[-1], ("fill", "4", 34.75))

    def test_a_failed_base_fill_never_writes_the_premium(self):
        """If the base cannot be reduced, adding the premium would inflate."""
        out, calls = self._run(
            [self._one_row_grid()], fill_results=[False, False, False]
        )
        self.assertIn("base_fill_failed", out["failed"][0])
        self.assertEqual([c for c in calls if c[0] == "fill" and c[1] == "5"], [])

    def test_a_transient_fill_miss_is_retried_not_failed(self):
        out, _calls = self._run(
            [self._one_row_grid(), self._one_row_grid()],
            fill_results=[False, True, True],
        )
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["applied"], [self.NAME])

    def test_add_row_is_scoped_to_the_row_whose_menu_was_opened(self):
        """ADP renders a menu per row; an unscoped click hits the first one.

        Live 2026-09-21: every "Add row" landed on the page's index-0 row —
        Alvarez on page 1, Krause on page 2 — so the row index must travel with
        the click, not just with the menu-open.
        """
        _out, calls = self._run([self._one_row_grid(), self._one_row_grid()])
        opened = [c for c in calls if c[0] == "menu"][0][1]
        clicked = [c for c in calls if c[0] == "item"][0][2]
        self.assertEqual(clicked, opened)

    def test_a_row_added_to_someone_else_is_named_as_such(self):
        """Grid grew but not on this employee: report it, do not write hours.

        This is distinct from "no row appeared" and used to be indistinguishable
        from it, which is why the misdirected clicks went unnoticed for hours.
        """
        out, calls = self._run(
            [self._one_row_grid()], grew=1, line_count_after_add=1,
            _rate2_row_indices=lambda p, *, employee, base_reg: (None, None),
        )
        self.assertEqual(out["applied"], [])
        self.assertIn("add_row_landed_elsewhere", out["failed"][0])
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_no_row_anywhere_is_still_reported_as_no_new_row(self):
        out, calls = self._run(
            [self._one_row_grid()], grew=0, line_count_after_add=1,
            _rate2_row_indices=lambda p, *, employee, base_reg: (None, None),
        )
        self.assertIn("no_new_row", out["failed"][0])
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_an_already_split_employee_is_skipped_not_split_again(self):
        # Rerunning against the same draft must not pay the uplift twice.
        already = {self.NAME: {
            "reg": 34.75, "hours": 34.75, "ot": 0.0, "rate": 15.25,
            "rows": [
                {"row_index": "4", "reg": 30.0, "ot": 0.0, "rate": 15.25},
                {"row_index": "5", "reg": 4.75, "ot": 0.0, "rate": 16.25},
            ],
        }}
        out, calls = self._run([already])
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["applied"], [self.NAME])
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_an_existing_split_with_the_wrong_total_is_not_taken_on_trust(self):
        """"Two rows" is not evidence of a *correct* two rows.

        A failed earlier run can leave rows that are present but wrong (live
        2026-09-21). Accepting them silently would pay those numbers.
        """
        wrong = {self.NAME: {
            "reg": 45.0, "hours": 45.0, "ot": 0.0, "rate": 15.25,
            "rows": [
                {"row_index": "4", "reg": 41.65, "ot": 0.0},
                {"row_index": "5", "reg": 3.35, "ot": 0.0},
            ],
        }}
        out, calls = self._run([wrong])
        self.assertEqual(out["applied"], [])
        self.assertIn("unexpected_existing_split", out["failed"][0])
        # Reported, never "repaired": no hours are written on a suspect split.
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_an_existing_split_totalling_right_but_on_wrong_hours_fails(self):
        """Right total, wrong division — neither row carries the solo hours."""
        skewed = {self.NAME: {
            "reg": 34.75, "hours": 34.75, "ot": 0.0, "rate": 15.25,
            "rows": [
                {"row_index": "4", "reg": 20.0, "ot": 0.0},
                {"row_index": "5", "reg": 14.75, "ot": 0.0},
            ],
        }}
        out, _calls = self._run([skewed])
        self.assertEqual(out["applied"], [])
        self.assertIn("unexpected_existing_split", out["failed"][0])

    def test_three_line_items_is_always_suspect(self):
        triple = {self.NAME: {
            "reg": 34.75, "hours": 34.75, "ot": 0.0, "rate": 15.25,
            "rows": [
                {"row_index": "4", "reg": 25.25, "ot": 0.0},
                {"row_index": "5", "reg": 4.75, "ot": 0.0},
                {"row_index": "6", "reg": 4.75, "ot": 0.0},
            ],
        }}
        out, _calls = self._run([triple])
        self.assertEqual(out["applied"], [])
        self.assertIn("unexpected_existing_split", out["failed"][0])

    def test_a_changed_total_is_reported_as_a_failure(self):
        # Verification re-reads the grid; 34.75 -> 39.50 means the base row was
        # never reduced, so the employee would be overpaid.
        out, _calls = self._run(
            [self._one_row_grid()],
            after_rows=[
                {"employee": self.NAME, "row_index": "4", "reg": 34.75, "ot": 0.0},
                {"employee": self.NAME, "row_index": "5", "reg": 4.75, "ot": 0.0},
            ],
        )
        self.assertEqual(out["applied"], [])
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("total_changed", out["failed"][0])

    def test_a_split_that_did_not_produce_two_lines_is_a_failure(self):
        """One line back means the insert vanished; never call that applied."""
        out, _calls = self._run(
            [self._one_row_grid()],
            after_rows=[
                {"employee": self.NAME, "row_index": "4", "reg": 30.0, "ot": 0.0}
            ],
            line_count_after_add=2,
        )
        self.assertEqual(out["applied"], [])
        self.assertIn("expected_2_funded_lines", out["failed"][0])

    def test_rows_are_re_resolved_after_the_insert_not_remembered(self):
        """The live 2026-09-21 defect: Add row renumbers every row below it.

        A stale base index pointed at a different employee's row and 41.65 h were
        written there. Both indices must come from the post-insert grid, so the
        fills must use what `_rate2_row_indices` returns — here deliberately
        different from the pre-insert index.
        """
        out, calls = self._run(
            [self._one_row_grid()],
            _rate2_row_indices=lambda p, *, employee, base_reg: ("7", "8"),
            after_rows=[
                {"employee": self.NAME, "row_index": "7", "reg": 30.0, "ot": 0.0,
                 "rate": 15.25},
                {"employee": self.NAME, "row_index": "8", "reg": 4.75, "ot": 0.0,
                 "rate": 16.25},
            ],
        )
        fills = [c for c in calls if c[0] == "fill"]
        self.assertEqual(fills, [("fill", "7", 30.0), ("fill", "8", 4.75)])
        self.assertEqual(out["failed"], [])
        # The rate is picked on the re-resolved new row too, not the stale one.
        self.assertIn(("rate", "8", 16.25), calls)

    def test_a_leftover_empty_row_is_reused_without_adding_another(self):
        """Live Alvarez shape. Reuse keeps repeated attempts from piling up rows."""
        with_debris = {self.NAME: {
            "reg": 34.75, "hours": 34.75, "ot": 0.0, "rate": 15.25,
            "rows": [
                {"row_index": "4", "reg": 34.75, "pers": 0.0, "hol": 0.0, "ot": 0.0},
                {"row_index": "5", "reg": 0.0, "pers": 0.0, "hol": 0.0, "ot": 0.0},
            ],
        }}
        out, calls = self._run([with_debris])
        self.assertEqual(out["failed"], [])
        self.assertEqual(out["applied"], [self.NAME])
        # No menu, no Add row — the empty line is already there.
        self.assertEqual([c for c in calls if c[0] in {"menu", "item"}], [])
        self.assertEqual(
            [c for c in calls if c[0] == "fill"],
            [("fill", "4", 30.0), ("fill", "5", 4.75)],
        )
        self.assertIn(("rate", "5", 16.25), calls)

    def test_one_employees_failure_does_not_get_retried(self):
        """A retry on a half-applied split would pay the premium twice."""
        out, calls = self._run(
            [self._one_row_grid()],
            _open_row_action_menu=lambda p, *, row_index: False,
        )
        self.assertEqual(len(out["failed"]), 1)
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_a_missing_add_row_item_fails_without_touching_hours(self):
        out, calls = self._run(
            [self._one_row_grid(), self._one_row_grid()],
            _click_menu_item=lambda p, label, *, test_id="", row_index="": False,
        )
        self.assertEqual(out["applied"], [])
        self.assertIn("no_add_row", out["failed"][0])
        self.assertEqual([c for c in calls if c[0] == "fill"], [])

    def test_add_row_is_clicked_by_adps_stable_test_id(self):
        # Text-only matching would also hit whatever other sdf-menu is open.
        seen: list[tuple] = []

        def _item(_page, label, *, test_id="", row_index=""):
            seen.append((label, test_id, row_index))
            return True

        self._run(
            [self._one_row_grid(), self._one_row_grid()],
            _click_menu_item=_item,
        )
        self.assertEqual(seen, [("Add row", "optionsAddRowButton", "4")])

    def test_nobody_eligible_is_a_no_op(self):
        with patch(f"{self.MOD}._merge_solo_hours", side_effect=lambda r, _p: r):
            from skills.adp_run_automation.payroll_draft_backend import (
                _apply_solo_rate2,
            )

            out = _apply_solo_rate2(self._Page(), [], premium_rate=16.25)
        self.assertEqual(out, {"applied": [], "failed": [], "premium_hours": 0.0})


class TestClassifyRate2State(unittest.TestCase):
    """What an employee's existing rows mean for the premium split."""

    def row(self, idx, reg=0.0, ot=0.0):
        return {"row_index": idx, "reg": reg, "pers": 0.0, "hol": 0.0, "ot": ot}

    def test_a_single_funded_row_is_split(self):
        plan = classify_rate2_state(
            [self.row("0", reg=9.2)], solo_hours=1.37, want_total=9.2
        )
        self.assertEqual(plan.verdict, "split")
        self.assertEqual((plan.base_index, plan.base_hours), ("0", 9.2))
        self.assertIsNone(plan.reuse_index)

    def test_leftover_empty_rows_are_reused_not_added_to(self):
        """The live Alvarez shape: 4 empty rows from failed Add-row attempts.

        Reuse keeps the attempt idempotent — otherwise every run leaves another
        empty line behind, which is exactly how it got to four.
        """
        plan = classify_rate2_state(
            [self.row("0", reg=9.2), self.row("1"), self.row("2"),
             self.row("3"), self.row("4")],
            solo_hours=1.37, want_total=9.2,
        )
        self.assertEqual(plan.verdict, "split")
        self.assertEqual(plan.base_index, "0")
        self.assertEqual(plan.reuse_index, "1")

    def test_a_correct_existing_split_is_left_alone(self):
        plan = classify_rate2_state(
            [self.row("0", reg=28.55), self.row("1", reg=0.97)],
            solo_hours=0.97, want_total=29.52,
        )
        self.assertEqual(plan.verdict, "already_split")

    def test_two_funded_rows_that_are_not_our_split_are_suspect(self):
        plan = classify_rate2_state(
            [self.row("0", reg=20.0), self.row("1", reg=9.52)],
            solo_hours=0.97, want_total=29.52,
        )
        self.assertEqual(plan.verdict, "suspect")
        self.assertIn("two_funded_rows_without_solo_line", plan.why)

    def test_hours_that_do_not_add_up_are_suspect(self):
        plan = classify_rate2_state(
            [self.row("0", reg=41.65)], solo_hours=11.48, want_total=53.13
        )
        self.assertEqual(plan.verdict, "suspect")
        self.assertIn("want_total=53.13", plan.why)

    def test_empty_rows_do_not_count_toward_the_total(self):
        """Debris must not make a correct total look wrong."""
        plan = classify_rate2_state(
            [self.row("0", reg=9.2), self.row("1")],
            solo_hours=1.37, want_total=9.2,
        )
        self.assertEqual(plan.verdict, "split")

    def test_a_row_with_only_overtime_is_funded_not_debris(self):
        plan = classify_rate2_state(
            [self.row("0", reg=40.0), self.row("1", ot=3.0)],
            solo_hours=2.0, want_total=40.0,
        )
        self.assertEqual(plan.verdict, "suspect")

    def test_a_half_applied_split_is_repaired_not_reported(self):
        """The live Huynh shape: premium row added, base row never reduced.

        She read 29.52 + 0.97 = 30.49 against an expected 29.52, so she gained
        0.97h at the premium rate instead of having them repriced. The base row
        is what needs rewriting; the premium row is already correct.
        """
        plan = classify_rate2_state(
            [self.row("0", reg=29.52), self.row("1", reg=0.97)],
            solo_hours=0.97, want_total=29.52,
        )
        self.assertEqual(plan.verdict, "repair")
        self.assertEqual(plan.base_index, "0")
        self.assertEqual(plan.reuse_index, "1")
        # base_hours is the *target* total, so rate2_split yields 28.55 / 0.97.
        self.assertEqual(plan.base_hours, 29.52)
        keep, premium = rate2_split(
            adp_regular_hours=plan.base_hours, solo_hours=0.97
        )
        self.assertEqual((keep, premium), (28.55, 0.97))

    def test_the_live_perales_shape_repairs_too(self):
        """Same, with a leftover empty row alongside."""
        plan = classify_rate2_state(
            [self.row("0", reg=25.2), self.row("1", reg=1.98), self.row("2")],
            solo_hours=1.98, want_total=25.2,
        )
        self.assertEqual(plan.verdict, "repair")
        self.assertEqual((plan.base_index, plan.reuse_index), ("0", "1"))

    def test_an_overshooting_total_that_is_not_the_premium_stays_suspect(self):
        """Only the exact half-split signature is repairable; guesses are not."""
        plan = classify_rate2_state(
            [self.row("0", reg=29.52), self.row("1", reg=5.0)],
            solo_hours=0.97, want_total=29.52,
        )
        self.assertEqual(plan.verdict, "suspect")

    def test_an_unknown_expected_total_is_never_split_blind(self):
        plan = classify_rate2_state(
            [self.row("0", reg=9.2)], solo_hours=1.37, want_total=None
        )
        self.assertEqual(plan.verdict, "suspect")


class TestAPunchEditStrandsTheRate2Line(unittest.TestCase):
    """An operator punch fix changes the premium owed, not the keyed line.

    Live 2026-09-21: the operator corrected missing punches mid-cycle. Garcia's
    solo time fell from 11.48h to 5.25h, but his rate-2 line still carried 11.48h
    at $16.25 with the base row never reduced. Both halves of the system called
    this unfixable — the classifier could not recognise a premium line whose hours
    matched nothing, and the guardrail would not clear a mismatch that no longer
    equalled the premium owed — so the draft stayed overstated by 11.48h.
    """

    NAME = "Garcia, Jacob"
    WANT_TOTAL = 53.13
    OWED = 5.25
    ON_LINE = 11.48

    def _lines(self):
        return [
            grid_row(self.NAME, row_index="8", rate=15.25, reg=self.WANT_TOTAL),
            grid_row(self.NAME, row_index="9", rate=16.25, reg=self.ON_LINE),
        ]

    def test_the_stale_premium_line_is_identified_by_its_rate(self):
        plan = classify_rate2_state(
            self._lines(), solo_hours=self.OWED,
            want_total=self.WANT_TOTAL, premium_rate=16.25,
        )
        self.assertEqual(plan.verdict, "repair")
        self.assertEqual(plan.reuse_index, "9")
        self.assertEqual(plan.base_index, "8")
        self.assertEqual(plan.base_hours, self.WANT_TOTAL)

    def test_the_repair_reprices_only_the_hours_now_owed(self):
        plan = classify_rate2_state(
            self._lines(), solo_hours=self.OWED,
            want_total=self.WANT_TOTAL, premium_rate=16.25,
        )
        keep, premium = rate2_split(
            adp_regular_hours=plan.base_hours, solo_hours=self.OWED
        )
        self.assertEqual(premium, self.OWED)
        self.assertEqual(round(keep + premium, 2), self.WANT_TOTAL)

    def test_without_the_rate_hint_it_is_still_refused(self):
        """Guards the fix: hours-only matching cannot see this row."""
        plan = classify_rate2_state(
            self._lines(), solo_hours=self.OWED, want_total=self.WANT_TOTAL,
        )
        self.assertEqual(plan.verdict, "suspect")

    def test_the_guardrail_clears_it_from_the_grids_own_line_hours(self):
        ours = {self.NAME: self.WANT_TOTAL}
        adp = {self.NAME: round(self.WANT_TOTAL + self.ON_LINE, 2)}
        self.assertEqual(
            unrepairable_hours_fails(
                ours, adp, {self.NAME: self.OWED},
                on_rate2_line={self.NAME: self.ON_LINE},
            ),
            [],
        )

    def test_the_premium_owed_alone_does_not_clear_it(self):
        """Why `on_rate2_line` exists: the owed figure no longer matches."""
        ours = {self.NAME: self.WANT_TOTAL}
        adp = {self.NAME: round(self.WANT_TOTAL + self.ON_LINE, 2)}
        self.assertEqual(
            len(unrepairable_hours_fails(ours, adp, {self.NAME: self.OWED})), 1
        )

    def test_a_right_total_on_a_wrong_premium_line_is_repaired_not_accepted(self):
        lines = [
            grid_row(self.NAME, row_index="8", rate=15.25, reg=41.65),
            grid_row(self.NAME, row_index="9", rate=16.25, reg=self.ON_LINE),
        ]
        plan = classify_rate2_state(
            lines, solo_hours=self.OWED,
            want_total=self.WANT_TOTAL, premium_rate=16.25,
        )
        self.assertEqual(plan.verdict, "repair")

    def test_a_correct_split_is_still_left_alone(self):
        lines = [
            grid_row(self.NAME, row_index="8", rate=15.25, reg=47.88),
            grid_row(self.NAME, row_index="9", rate=16.25, reg=self.OWED),
        ]
        plan = classify_rate2_state(
            lines, solo_hours=self.OWED,
            want_total=self.WANT_TOTAL, premium_rate=16.25,
        )
        self.assertEqual(plan.verdict, "already_split")


class TestExpectedGrossIncludesThePremium(unittest.TestCase):
    """Expected Gross must price premium hours at the premium.

    Live 2026-09-21: every hour was priced at base, so the five employees who had
    a premium keyed each reported a gross_mismatch whose delta was exactly their
    premium. ADP was right in all five. A summary that cries wolf on known-good
    numbers is worse than no summary.
    """

    def _row(self, *, rate=15.25, solo=0.0, wages=0.0, tips=0.0,
             bonus=0.0, perks=0.0):
        rows = [{
            "employee": "Garcia, Jacob", "labor_type": "Part-time",
            "hours_worked": 53.13, "ot_hours": 0, "wage_rate_dollars": rate,
            "tips_allocated": tips, "review_bonus": bonus,
            "recognition_bonus": 0, "perks": perks,
            "solo_hours": solo, "solo_eligible": solo > 0,
            "est_gross_pay": wages,
        }]
        with patch(
            "skills.adp_run_automation.payroll_draft_backend._merge_solo_hours",
            side_effect=lambda r, _p: r,
        ):
            return packet_from_view_rows(rows)[0]

    def test_the_uplift_is_the_rate_difference_not_the_premium_rate(self):
        self.assertEqual(
            solo_premium_uplift_dollars(
                solo_hours=5.25, wage_rate=15.25, premium_rate=16.25
            ),
            5.25,
        )

    def test_the_five_live_deltas_are_reproduced(self):
        """Each employee's ADP-minus-ours delta, from the 2026-09-21 preview."""
        for solo, delta in (
            (5.25, 5.25),   # Garcia
            (4.13, 4.13),   # Huynh
            (1.55, 1.55),   # Guerrero
            (1.98, 1.98),   # Perales
            (1.12, 1.12),   # Priyosha
        ):
            self.assertEqual(
                solo_premium_uplift_dollars(
                    solo_hours=solo, wage_rate=15.25, premium_rate=16.25
                ),
                delta,
            )

    def test_an_employee_already_at_the_premium_rate_gets_no_uplift(self):
        """Johnson/Ortiz base at $16.25 — there is no premium to add."""
        self.assertEqual(
            solo_premium_uplift_dollars(
                solo_hours=4.0, wage_rate=16.25, premium_rate=16.25
            ),
            0.0,
        )

    def test_no_solo_hours_means_no_uplift(self):
        self.assertEqual(
            solo_premium_uplift_dollars(
                solo_hours=0.0, wage_rate=15.25, premium_rate=16.25
            ),
            0.0,
        )

    def test_expected_gross_adds_the_premium_to_wages_and_additions(self):
        row = self._row(solo=5.25, wages=810.23, tips=138.53)
        self.assertEqual(
            expected_gross_dollars(row, premium_rate=16.25), 954.01
        )

    def test_with_the_premium_off_expected_gross_stays_at_base(self):
        """Feature off means ADP has no rate-2 line, so neither should we."""
        row = self._row(solo=5.25, wages=810.23, tips=138.53)
        self.assertEqual(expected_gross_dollars(row, premium_rate=0.0), 948.76)

    def test_the_summary_tally_uses_the_same_definition_as_the_lines(self):
        """The drift this consolidates: two copies of one sum."""
        import inspect
        from skills.adp_run_automation.payroll_draft_backend import (
            _print_hours_wages_compare,
        )
        src = inspect.getsource(_print_hours_wages_compare)
        self.assertEqual(src.count("expected_gross_dollars(row, premium_rate="), 2)


class TestRate2RowsCanStraddleAGridPage(unittest.TestCase):
    """A row index is only clickable while its own page is displayed.

    Live 2026-09-21: Huynh's base row (42.68h) sat on one grid page and her
    0.97h premium row on the next. The page-local read saw only the fragment on
    screen, so she measured 0.97h against an expected 42.68h and was refused as
    "unexpected_existing_split" — the one row that actually needed repairing.
    """

    MOD = "skills.adp_run_automation.payroll_draft_backend"

    def test_the_orchestrator_reads_every_page_not_the_displayed_one(self):
        import inspect
        from skills.adp_run_automation.payroll_draft_backend import (
            _apply_solo_rate2,
        )
        src = inspect.getsource(_apply_solo_rate2)
        self.assertIn("_paginate_timecard_detail(page)", src)
        self.assertNotIn("_ag_enter_page_hours(page)", src)

    def test_a_row_is_shown_before_it_is_filled(self):
        import inspect
        from skills.adp_run_automation.payroll_draft_backend import (
            _apply_solo_rate2,
        )
        src = inspect.getsource(_apply_solo_rate2)
        self.assertIn("_grid_goto_page(page, row_page.get(str(idx), base_page))", src)
        # Never page 0 as the fallback: a freshly added row is absent from
        # `row_page` and lands beside the base row, not on the first page.
        self.assertNotIn("row_page.get(str(idx), 0)", src)

    def test_verification_spans_pages_too(self):
        import inspect
        from skills.adp_run_automation.payroll_draft_backend import (
            _apply_solo_rate2,
        )
        src = inspect.getsource(_apply_solo_rate2)
        self.assertIn("_employee_rows_all_pages(page, name)", src)

    def test_each_row_carries_the_page_it_was_read_from(self):
        import inspect
        from skills.adp_run_automation.payroll_draft_backend import (
            _paginate_timecard_detail,
        )
        self.assertIn(
            'row["page_no"] = page_no', inspect.getsource(_paginate_timecard_detail)
        )

    def test_the_straddled_shape_classifies_as_a_repair(self):
        """Given both rows, Huynh's live shape is repairable, not suspect."""
        lines = [
            grid_row("Huynh, Hillary", row_index="9", rate=15.25, reg=42.68),
            grid_row("Huynh, Hillary", row_index="0", rate=16.25, reg=0.97),
        ]
        plan = classify_rate2_state(
            lines, solo_hours=4.13, want_total=42.68, premium_rate=16.25,
        )
        self.assertEqual(plan.verdict, "repair")
        self.assertEqual(plan.reuse_index, "0")
        keep, premium = rate2_split(
            adp_regular_hours=plan.base_hours, solo_hours=4.13
        )
        self.assertEqual((keep, premium), (38.55, 4.13))

    def test_the_fragment_alone_is_refused(self):
        """What the page-local read saw, and why it failed her."""
        lines = [grid_row("Huynh, Hillary", row_index="0", rate=16.25, reg=0.97)]
        plan = classify_rate2_state(
            lines, solo_hours=4.13, want_total=42.68, premium_rate=16.25,
        )
        self.assertEqual(plan.verdict, "suspect")


class TestExtraRateLineHours(unittest.TestCase):
    """Hours parked on premium-rated lines, read off the grid."""

    def test_it_reports_the_premium_line_not_the_base(self):
        detail = {
            "Garcia, Jacob": {"hours": 64.61, "rows": [
                grid_row("Garcia, Jacob", row_index="8", rate=15.25, reg=53.13),
                grid_row("Garcia, Jacob", row_index="9", rate=16.25, reg=11.48),
            ]},
        }
        self.assertEqual(
            extra_rate_line_hours(detail, premium_rate=16.25),
            {"Garcia, Jacob": 11.48},
        )

    def test_a_single_line_employee_is_absent(self):
        detail = {
            "Krause, Lindsay": {"hours": 79.75, "rows": [
                grid_row("Krause, Lindsay", row_index="4", rate=25.0,
                         reg=76.87, ot=2.88),
            ]},
        }
        self.assertEqual(extra_rate_line_hours(detail, premium_rate=16.25), {})

    def test_an_employee_already_at_the_premium_base_rate_is_not_counted(self):
        """Johnson/Ortiz sit at $16.25 as their base — one line, no split."""
        detail = {
            "Johnson, Dolce J": {"hours": 54.73, "rows": [
                grid_row("Johnson, Dolce J", row_index="6", rate=16.25, reg=54.73),
            ]},
        }
        self.assertEqual(extra_rate_line_hours(detail, premium_rate=16.25), {})

    def test_empty_premium_lines_contribute_nothing(self):
        detail = {
            "Perales, Elizabeth": {"hours": 25.2, "rows": [
                grid_row("Perales, Elizabeth", row_index="1", rate=15.25, reg=25.2),
                grid_row("Perales, Elizabeth", row_index="3", rate=16.25, reg=0.0),
            ]},
        }
        self.assertEqual(extra_rate_line_hours(detail, premium_rate=16.25), {})


class TestUnrepairableHoursFails(unittest.TestCase):
    """Which guardrail failures must keep blocking the split.

    The deadlock this breaks: the hours guardrail blocks the split, and a
    half-applied split breaks the hours guardrail, so a failed run leaves a state
    no rerun can fix. Live 2026-09-21 that stranded Huynh and Perales.
    """

    def test_a_half_applied_split_does_not_block(self):
        remaining = unrepairable_hours_fails(
            {"Huynh, Hillary": 29.52, "Perales, Elizabeth": 25.2},
            {"Huynh, Hillary": 30.49, "Perales, Elizabeth": 27.18},
            {"Huynh, Hillary": 0.97, "Perales, Elizabeth": 1.98},
        )
        self.assertEqual(remaining, [])

    def test_a_real_disagreement_still_blocks(self):
        """Extra hours unrelated to the premium are a genuine mismatch."""
        remaining = unrepairable_hours_fails(
            {"Garcia, Jacob": 53.13},
            {"Garcia, Jacob": 60.0},
            {"Garcia, Jacob": 11.48},
        )
        self.assertEqual(len(remaining), 1)
        self.assertIn("Garcia", remaining[0])

    def test_an_employee_owed_no_premium_still_blocks(self):
        remaining = unrepairable_hours_fails(
            {"Krause, Lindsay": 71.17}, {"Krause, Lindsay": 72.17}, {}
        )
        self.assertEqual(len(remaining), 1)

    def test_hours_short_on_adp_still_blocks(self):
        """A half-split only ever *inflates*; a shortfall is something else."""
        remaining = unrepairable_hours_fails(
            {"Huynh, Hillary": 29.52},
            {"Huynh, Hillary": 28.55},
            {"Huynh, Hillary": 0.97},
        )
        self.assertEqual(len(remaining), 1)

    def test_a_missing_employee_still_blocks(self):
        remaining = unrepairable_hours_fails(
            {"Huynh, Hillary": 29.52}, {}, {"Huynh, Hillary": 0.97}
        )
        self.assertEqual(len(remaining), 1)
        self.assertIn("hours_missing_on_adp", remaining[0])

    def test_one_repairable_and_one_real_failure_still_blocks(self):
        remaining = unrepairable_hours_fails(
            {"Huynh, Hillary": 29.52, "Garcia, Jacob": 53.13},
            {"Huynh, Hillary": 30.49, "Garcia, Jacob": 60.0},
            {"Huynh, Hillary": 0.97, "Garcia, Jacob": 11.48},
        )
        self.assertEqual(len(remaining), 1)
        self.assertIn("Garcia", remaining[0])

    def test_clean_hours_yield_nothing_to_block_on(self):
        remaining = unrepairable_hours_fails(
            {"Huynh, Hillary": 29.52},
            {"Huynh, Hillary": 29.52},
            {"Huynh, Hillary": 0.97},
        )
        self.assertEqual(remaining, [])


class TestRowMenuClicksAreVisibilityGated(unittest.TestCase):
    """Every row has a menu in the DOM, so "matches" never means "is open".

    Live 2026-09-21: `document.querySelector('[data-test-id=...]')` returned the
    first row's Add-row item regardless of which menu was open, so the split was
    applied to whichever employee sat at index 0 of the page.
    """

    def _src(self, fn_name: str) -> str:
        import inspect

        from skills.adp_run_automation import payroll_draft_backend as mod

        return inspect.getsource(getattr(mod, fn_name))

    def test_menu_item_click_tries_the_row_before_the_document(self):
        """Row scoping is proof of correctness; visibility is the fallback's proof."""
        src = self._src("_click_menu_item")
        self.assertIn("filter(visible)", src)
        self.assertIn("_JS_VISIBLE", src)
        self.assertIn("rowIndex", src)
        # The ladder must reach the row before any document-wide strategy.
        for earlier, later in (
            ("'row_visible'", "'row_any'"),
            ("'row_any'", "'doc_by_row'"),
            ("'doc_by_row'", "'doc_visible'"),
        ):
            self.assertLess(
                src.index(earlier), src.index(later),
                f"{earlier} must be tried before {later}",
            )

    def test_a_miss_reports_what_it_saw(self):
        """Diagnostics in the failure path, so a retry needs no extra login."""
        src = self._src("_click_menu_item")
        self.assertIn("menu_item_miss", src)
        self.assertIn("candidates", src)

    def test_menu_item_click_has_no_bare_document_lookup_by_test_id(self):
        """The unscoped, unguarded test-id lookup is the exact bug; keep it gone.

        Looking the *row* up on `document` is fine and necessary — it is the
        menu-item lookup that must be rooted at a node and visibility-checked.
        """
        src = self._src("_click_menu_item")
        self.assertNotIn("document.querySelector('[data-test-id=", src)
        self.assertNotIn('document.querySelectorAll(\'[data-test-id=', src)

    def test_rate_picker_also_ignores_unrendered_options(self):
        """Each collapsed selector holds its rates, so an open-menu test is needed."""
        src = self._src("_select_available_rate")
        self.assertIn("if (!visible(el)) continue;", src)

    def test_the_visibility_helper_rejects_a_zero_box_element(self):
        """Client rects, not CSS guesses: a closed menu's items have none."""
        from skills.adp_run_automation import payroll_draft_backend as mod

        self.assertIn("getClientRects", mod._JS_VISIBLE)
        self.assertIn("aria-hidden", mod._JS_VISIBLE)


class TestSoloGapIsWiredIntoThePreview(unittest.TestCase):
    """#309: the staleness gate lives in a different function than the check.

    Live 2026-09-21: `solo_gap` was computed in `run_draft` but read inside
    `run_live_preview`, so the gate raised `NameError: name 'solo_gap' is not
    defined` the first time a run got past the hours guardrail — after keying the
    money columns, killing the Preview. A gate that cannot be evaluated is worse
    than no gate: it failed on the one path it was built to protect.
    """

    def test_run_live_preview_accepts_solo_gap(self):
        import inspect

        from skills.adp_run_automation.payroll_draft_backend import run_live_preview

        self.assertIn(
            "solo_gap", inspect.signature(run_live_preview).parameters,
        )

    def test_run_draft_passes_solo_gap_through(self):
        import inspect

        from skills.adp_run_automation import payroll_draft_backend as mod

        src = inspect.getsource(mod.run_draft)
        self.assertIn("solo_gap=solo_gap", src)

    def test_the_gate_reads_a_bound_name(self):
        """Compile-level proof: no free variable named solo_gap in the preview."""
        from skills.adp_run_automation import payroll_draft_backend as mod

        code = mod.run_live_preview.__code__
        names = set(code.co_varnames) | set(code.co_names)
        self.assertIn("solo_gap", code.co_varnames, f"not a local; names={names}")


class TestSoloCoverageGap(unittest.TestCase):
    """#309: solo hours drifting behind punches understates the premium."""

    def test_compares_minutes_not_the_null_prone_scrape_timestamp(self):
        # adp_punches.scraped_at_utc is only stamped by the Sync-clocked-hours
        # path, so it was NULL for every date in the 09-07..09-20 cycle; a
        # built_at < scraped_at test would have passed on all of them.
        import inspect

        from skills.adp_run_automation import payroll_draft_backend as mod

        src = inspect.getsource(mod.solo_coverage_gap)
        self.assertIn("SUM(total_minutes)", src)
        self.assertIn("ABS(s.solo_min - p.punch_min)", src)

    def test_reports_punch_dates_with_no_solo_row(self):
        with patch(
            "core.datastore.read_query",
            return_value=[{"d": "2026-09-19"}, {"d": "2026-09-20"}],
        ):
            from skills.adp_run_automation.payroll_draft_backend import (
                solo_coverage_gap,
            )

            self.assertEqual(
                solo_coverage_gap("2026-09-07", "2026-09-20"),
                ["2026-09-19", "2026-09-20"],
            )

    def test_full_coverage_is_an_empty_gap(self):
        with patch("core.datastore.read_query", return_value=[]):
            from skills.adp_run_automation.payroll_draft_backend import (
                solo_coverage_gap,
            )

            self.assertEqual(solo_coverage_gap("2026-09-07", "2026-09-20"), [])

    def test_an_unreadable_check_is_stale_not_clean(self):
        # Treating an error as "no gap" would let the draft key money it cannot
        # vouch for — the opposite of why the check exists.
        with patch("core.datastore.read_query", side_effect=RuntimeError("boom")):
            from skills.adp_run_automation.payroll_draft_backend import (
                solo_coverage_gap,
            )

            self.assertEqual(
                solo_coverage_gap("2026-09-07", "2026-09-20"), ["unknown"]
            )


class TestSoloRate2IsGatedOnTheHoursGuardrail(unittest.TestCase):
    """The hours split must not run on a grid that disagrees with the console.

    Pins the source line rather than driving a browser: the first live proof
    (2026-09-16) applied the split with 7 guardrail failures outstanding because
    the condition read `fill_ok`, which stays True through a guardrail failure so
    that money columns can still be filled.
    """

    def _src(self):
        import inspect

        from skills.adp_run_automation import payroll_draft_backend as mod

        return inspect.getsource(mod)

    def test_condition_requires_no_unexplained_guardrail_failures(self):
        self.assertIn(
            "if fill_ok and not blocking and not solo_gap "
            "and solo_rate2_enabled():",
            self._src(),
            "the solo hours split must be gated on the guardrail AND fresh solo data",
        )

    def test_the_gate_is_never_fill_ok_alone(self):
        src = self._src()
        self.assertNotIn("if fill_ok and not solo_gap", src)
        self.assertNotIn("if fill_ok and solo_rate2_enabled()", src)

    def test_blocking_is_the_guardrail_minus_only_repairable_half_splits(self):
        """`blocking` must come from the vetted helper, not a looser test."""
        src = self._src()
        self.assertIn("unrepairable_hours_fails(", src)
        self.assertIn("ours, adp_hours, solo_by_name,", src)
        # The grid's own rate-2 line hours, not the premium owed: an operator
        # punch edit moves the latter and leaves the former stale.
        self.assertIn("on_rate2_line=on_rate2_line,", src)
        self.assertIn("else guardrail_fails", src)

    def test_hours_are_re_checked_after_the_split(self):
        """A correct split leaves totals unchanged, so the guardrail proves it."""
        src = self._src()
        self.assertIn("post_rate2_guardrail", src)
        self.assertIn(
            "guardrail_fails = hours_guardrail_failures(ours, adp_hours)", src
        )


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
