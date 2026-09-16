"""Tests for solo-shift hour attribution (Issue #309).

Each agreed jam decision (D1-D4, D6) has a named test so the PR evidence pack can
cite a scenario rather than "all green".
"""

from __future__ import annotations

import random
import unittest

from skills.bhaga_labor.solo_shift import (
    SoloConfig,
    attribute_day,
    intervals_from_punches,
    merge_intervals,
    parse_hhmm,
    punch_interval,
    solo_blocks,
)

# Mirrors the seeded bhaga.store_config values; the module itself holds no literals.
CONFIG = SoloConfig(
    min_block_minutes=15,
    eligible_base_rate_cents=1525,
    premium_rate_cents=1625,
    effective_date="2026-09-07",
)

DATE = "2026-09-10"
ELIGIBLE = 1525
ALREADY_PREMIUM = 1625
MANAGER = 2500


def hm(text: str) -> int:
    return parse_hhmm(text)  # type: ignore[return-value]


class TestParsing(unittest.TestCase):
    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("00:00"), 0)
        self.assertEqual(parse_hhmm("16:01"), 961)
        self.assertEqual(parse_hhmm(" 9:05 "), 545)

    def test_parse_hhmm_rejects_malformed(self):
        for bad in (None, "", "  ", "nope", "25:00", "12:99", "12"):
            self.assertIsNone(parse_hhmm(bad), bad)

    def test_overnight_punch_gets_plus_24h(self):
        # Matches coverage-model.ts:162 — an out_time <= in_time crossed midnight.
        self.assertEqual(punch_interval("22:00", "02:00"), (1320, 1560))

    def test_unparseable_punch_is_none(self):
        self.assertIsNone(punch_interval("garbage", "02:00"))
        self.assertIsNone(punch_interval("22:00", None))


class TestMergeIntervals(unittest.TestCase):
    def test_split_punches_coalesce(self):
        self.assertEqual(
            merge_intervals([(600, 700), (700, 800)]), [(600, 800)]
        )

    def test_overlapping_coalesce_to_outer_bound(self):
        self.assertEqual(merge_intervals([(600, 900), (650, 700)]), [(600, 900)])

    def test_gap_is_preserved(self):
        self.assertEqual(
            merge_intervals([(600, 700), (800, 900)]), [(600, 700), (800, 900)]
        )

    def test_zero_length_dropped(self):
        self.assertEqual(merge_intervals([(600, 600)]), [])


class TestSoloAttribution(unittest.TestCase):
    def attribute(self, intervals, rates, date=DATE, config=CONFIG):
        return {r.employee: r for r in attribute_day(date, intervals, rates, config)}

    def test_happy_path_one_person_all_day_is_all_solo(self):
        rows = self.attribute(
            {"Solo, Sam": [(hm("09:00"), hm("17:00"))]}, {"Solo, Sam": ELIGIBLE}
        )
        row = rows["Solo, Sam"]
        self.assertEqual(row.total_minutes, 480)
        self.assertEqual(row.solo_minutes, 480)
        self.assertEqual(row.team_minutes, 0)
        self.assertTrue(row.eligible)
        self.assertEqual(row.premium_cents, 800)  # 8h x $1.00

    def test_fully_overlapping_shifts_have_no_solo(self):
        rows = self.attribute(
            {
                "A, One": [(hm("09:00"), hm("17:00"))],
                "B, Two": [(hm("09:00"), hm("17:00"))],
            },
            {"A, One": ELIGIBLE, "B, Two": ELIGIBLE},
        )
        for row in rows.values():
            self.assertEqual(row.solo_minutes, 0)
            self.assertEqual(row.team_minutes, row.total_minutes)
            self.assertEqual(row.premium_cents, 0)

    def test_handoff_sliver_is_folded_into_team_not_paid(self):
        # D1: the 3-minute changeover artifact that made raw occupancy untrustworthy.
        rows = self.attribute(
            {
                "Early, Bird": [(hm("09:00"), hm("13:03"))],
                "Late, Riser": [(hm("13:00"), hm("17:00"))],
            },
            {"Early, Bird": ELIGIBLE, "Late, Riser": ELIGIBLE},
        )
        early = rows["Early, Bird"]
        # 09:00-13:00 alone is a real 240-minute block; only the 3-min tail is noise.
        self.assertEqual(early.solo_minutes, 240)
        self.assertEqual(early.team_minutes, 3)
        late = rows["Late, Riser"]
        self.assertEqual(late.solo_minutes, 237)
        self.assertEqual(late.team_minutes, 3)

    def test_exactly_threshold_block_counts(self):
        rows = self.attribute(
            {
                "A, One": [(hm("09:00"), hm("09:15"))],
                "B, Two": [(hm("09:15"), hm("17:00"))],
            },
            {"A, One": ELIGIBLE, "B, Two": ELIGIBLE},
        )
        self.assertEqual(rows["A, One"].solo_minutes, 15)

    def test_below_threshold_block_does_not_count(self):
        rows = self.attribute(
            {
                "A, One": [(hm("09:00"), hm("09:14"))],
                "B, Two": [(hm("09:14"), hm("17:00"))],
            },
            {"A, One": ELIGIBLE, "B, Two": ELIGIBLE},
        )
        row = rows["A, One"]
        self.assertEqual(row.solo_minutes, 0)
        self.assertEqual(row.team_minutes, 14)
        self.assertEqual(row.premium_cents, 0)

    def test_split_punch_solo_only_in_midday_gap(self):
        # Coworker steps out 12:00-14:00; the closer is alone only then.
        rows = self.attribute(
            {
                "Closer, Cass": [(hm("09:00"), hm("17:00"))],
                "Helper, Hal": [(hm("09:00"), hm("12:00")), (hm("14:00"), hm("17:00"))],
            },
            {"Closer, Cass": ELIGIBLE, "Helper, Hal": ELIGIBLE},
        )
        cass = rows["Closer, Cass"]
        self.assertEqual(cass.solo_minutes, 120)
        self.assertEqual(cass.total_minutes, 480)
        self.assertEqual(rows["Helper, Hal"].solo_minutes, 0)
        self.assertEqual(rows["Helper, Hal"].total_minutes, 360)

    def test_manager_presence_means_not_solo(self):
        # D3: the manager is labor-excluded but still physically company.
        rows = self.attribute(
            {
                "Hourly, Hank": [(hm("09:00"), hm("17:00"))],
                "Krause, Lindsay": [(hm("09:00"), hm("17:00"))],
            },
            {"Hourly, Hank": ELIGIBLE, "Krause, Lindsay": MANAGER},
        )
        self.assertEqual(rows["Hourly, Hank"].solo_minutes, 0)
        self.assertEqual(rows["Hourly, Hank"].premium_cents, 0)

    def test_manager_alone_accrues_solo_but_no_premium(self):
        # D4: tracked for the labor view, never paid.
        rows = self.attribute(
            {"Krause, Lindsay": [(hm("09:00"), hm("17:00"))]},
            {"Krause, Lindsay": MANAGER},
        )
        row = rows["Krause, Lindsay"]
        self.assertEqual(row.solo_minutes, 480)
        self.assertFalse(row.eligible)
        self.assertEqual(row.premium_cents, 0)

    def test_already_at_premium_rate_accrues_solo_but_no_premium(self):
        # D2: announcement was "from $15.25 to $16.25" — they are already there.
        rows = self.attribute(
            {"Johnson, Dolce": [(hm("09:00"), hm("17:00"))]},
            {"Johnson, Dolce": ALREADY_PREMIUM},
        )
        row = rows["Johnson, Dolce"]
        self.assertEqual(row.solo_minutes, 480)
        self.assertFalse(row.eligible)
        self.assertEqual(row.premium_cents, 0)

    def test_unknown_base_rate_earns_no_premium(self):
        rows = self.attribute(
            {"New, Hire": [(hm("09:00"), hm("17:00"))]}, {}
        )
        self.assertFalse(rows["New, Hire"].eligible)
        self.assertEqual(rows["New, Hire"].premium_cents, 0)

    def test_before_effective_date_earns_no_premium(self):
        # D6: the premium starts with the 2026-09-07 cycle.
        rows = self.attribute(
            {"Solo, Sam": [(hm("09:00"), hm("17:00"))]},
            {"Solo, Sam": ELIGIBLE},
            date="2026-09-06",
        )
        row = rows["Solo, Sam"]
        self.assertEqual(row.solo_minutes, 480)
        self.assertFalse(row.eligible)
        self.assertEqual(row.premium_cents, 0)

    def test_effective_date_boundary_is_inclusive(self):
        rows = self.attribute(
            {"Solo, Sam": [(hm("09:00"), hm("17:00"))]},
            {"Solo, Sam": ELIGIBLE},
            date="2026-09-07",
        )
        self.assertTrue(rows["Solo, Sam"].eligible)

    def test_empty_day_returns_no_rows(self):
        self.assertEqual(attribute_day(DATE, {}, {}, CONFIG), [])

    def test_thresholds_come_from_config_not_literals(self):
        # A 20-minute block qualifies at 15 but not at 30.
        intervals = {
            "A, One": [(hm("09:00"), hm("09:20"))],
            "B, Two": [(hm("09:20"), hm("17:00"))],
        }
        rates = {"A, One": ELIGIBLE, "B, Two": ELIGIBLE}
        lenient = self.attribute(intervals, rates)
        strict = self.attribute(
            intervals, rates, config=CONFIG._replace(min_block_minutes=30)
        )
        self.assertEqual(lenient["A, One"].solo_minutes, 20)
        self.assertEqual(strict["A, One"].solo_minutes, 0)


class TestReconciliationInvariant(unittest.TestCase):
    """solo + team == total, exact at integer minutes (bhaga.mdc invariant 2)."""

    def test_holds_on_randomized_interval_sets(self):
        rng = random.Random(309)
        for _ in range(400):
            intervals: dict[str, list[tuple[int, int]]] = {}
            for i in range(rng.randint(1, 5)):
                punches = []
                for _ in range(rng.randint(1, 3)):
                    start = rng.randint(0, 1300)
                    punches.append((start, start + rng.randint(1, 120)))
                intervals[f"E{i}"] = punches
            rows = attribute_day(DATE, intervals, {}, CONFIG)
            for row in rows:
                self.assertEqual(
                    row.solo_minutes + row.team_minutes,
                    row.total_minutes,
                    f"breach for {row.employee}: {row}",
                )
                self.assertGreaterEqual(row.solo_minutes, 0)
                self.assertGreaterEqual(row.team_minutes, 0)

    def test_solo_minutes_never_exceed_total(self):
        rows = attribute_day(
            DATE,
            {"A, One": [(hm("09:00"), hm("12:00")), (hm("11:00"), hm("14:00"))]},
            {},
            CONFIG,
        )
        row = rows[0]
        self.assertEqual(row.total_minutes, 300)  # overlap merged, not summed
        self.assertLessEqual(row.solo_minutes, row.total_minutes)


class TestSoloBlocks(unittest.TestCase):
    def test_returns_maximal_contiguous_runs(self):
        blocks = solo_blocks(
            {
                "A, One": [(0, 100)],
                "B, Two": [(40, 60)],
            }
        )
        self.assertEqual(blocks["A, One"], [(0, 40), (60, 100)])

    def test_no_solo_when_always_two_present(self):
        self.assertEqual(solo_blocks({"A": [(0, 100)], "B": [(0, 100)]}), {})


class TestIntervalsFromPunches(unittest.TestCase):
    def test_alias_spellings_resolve_to_one_employee(self):
        # adp_scheduled_shifts says "Johnson, Dolce J"; punches/rates say "Johnson, Dolce".
        aliases = {
            "Johnson, Dolce J": "Johnson, Dolce",
            "Johnson, Dolce": "Johnson, Dolce",
        }
        grouped = intervals_from_punches(
            [
                {"date": DATE, "employee_name": "Johnson, Dolce J",
                 "in_time": "09:00", "out_time": "12:00"},
                {"date": DATE, "employee_name": "Johnson, Dolce",
                 "in_time": "13:00", "out_time": "17:00"},
            ],
            aliases=aliases,
        )
        self.assertEqual(list(grouped[DATE]), ["Johnson, Dolce"])
        self.assertEqual(len(grouped[DATE]["Johnson, Dolce"]), 2)

    def test_malformed_punch_row_is_skipped_not_zeroed(self):
        grouped = intervals_from_punches(
            [
                {"date": DATE, "employee_name": "A, One",
                 "in_time": None, "out_time": "17:00"},
                {"date": DATE, "employee_name": "B, Two",
                 "in_time": "09:00", "out_time": "17:00"},
            ]
        )
        self.assertNotIn("A, One", grouped[DATE])
        self.assertIn("B, Two", grouped[DATE])

    def test_rows_without_date_or_name_are_skipped(self):
        grouped = intervals_from_punches(
            [
                {"date": "", "employee_name": "A, One",
                 "in_time": "09:00", "out_time": "17:00"},
                {"date": DATE, "employee_name": "",
                 "in_time": "09:00", "out_time": "17:00"},
            ]
        )
        self.assertEqual(grouped, {})

    def test_canonical_name_column_is_accepted(self):
        grouped = intervals_from_punches(
            [{"date": DATE, "canonical_name": "A, One",
              "in_time": "09:00", "out_time": "17:00"}]
        )
        self.assertIn("A, One", grouped[DATE])


if __name__ == "__main__":
    unittest.main()
