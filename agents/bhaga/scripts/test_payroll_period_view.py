"""String/math guards for migration 059 payroll period view."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL = (ROOT / "core/migrations/059_payroll_period_roster_perks.sql").read_text()


class TestPayrollPeriodViewSql(unittest.TestCase):
    def test_unions_shifts_and_perks(self):
        self.assertIn("employee_perks", SQL)
        self.assertIn("adp_shifts", SQL)
        self.assertIn("UNION ALL", SQL)
        self.assertIn("Full-time", SQL)
        self.assertIn("Krause, Lindsay", SQL)
        self.assertIn("2000 AS amount_cents", SQL)
        self.assertIn("GREATEST", SQL)


class TestPayrollPerkLabelsSql(unittest.TestCase):
    def test_encodes_id_and_dollars(self):
        sql = (ROOT / "core/migrations/060_payroll_perk_labels.sql").read_text()
        self.assertIn("CONCAT(perk_id, ':',", sql)
        self.assertNotIn("CREATE TABLE", sql)


class TestPayrollPaidHoursSql(unittest.TestCase):
    def test_pt_hours_prefer_shifts_over_tip_eligible(self):
        sql = (ROOT / "core/migrations/061_payroll_paid_hours.sql").read_text()
        self.assertIn("COALESCE(sh.hours_worked, t.hours_worked)", sql)
        self.assertIn("t.our_calc AS tips_allocated", sql)
        self.assertNotIn("CREATE TABLE", sql)

    def test_does_not_drop_recognition_join(self):
        self.assertIn("recognition_bonuses", SQL)
        self.assertIn("model_tip_alloc_period", SQL)


class TestPayrollFullRosterSql(unittest.TestCase):
    def test_includes_all_punchers_and_open_hours_through_yesterday(self):
        sql = (ROOT / "core/migrations/062_payroll_full_roster.sql").read_text()
        self.assertIn("punch_rows", sql)
        self.assertIn("hours_end", sql)
        self.assertIn("DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 1 DAY)", sql)
        self.assertNotIn("ft_rows", sql)
        self.assertIn("recognition_bonuses", sql)


class TestPayroll1to1RosterSql(unittest.TestCase):
    def test_carry_zero_hour_and_biweek_window_people(self):
        sql = (ROOT / "core/migrations/063_payroll_1to1_roster.sql").read_text()
        self.assertIn("carry_rows", sql)
        self.assertIn("window_people", sql)
        self.assertIn("INTERVAL 28 DAY", sql)
        self.assertIn("DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 1 DAY)", sql)


class TestPayrollAdpWageRoundingSql(unittest.TestCase):
    def test_numeric_round_not_float64(self):
        sql = (ROOT / "core/migrations/064_payroll_adp_wage_rounding.sql").read_text()
        self.assertIn("CAST(w.wage_rate_dollars AS NUMERIC)", sql)
        self.assertIn("CAST(GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) AS NUMERIC)", sql)
        self.assertNotIn(
            "GREATEST(r.hours_worked - COALESCE(r.ot_hours, 0), 0) * w.wage_rate_dollars",
            sql,
        )


class TestEmployeePerksPayPeriodSql(unittest.TestCase):
    def test_period_scoped_join(self):
        sql = (ROOT / "core/migrations/068_employee_perks_pay_period.sql").read_text()
        self.assertIn("ADD COLUMN IF NOT EXISTS pay_period", sql)
        self.assertIn("IFNULL(e.pay_period, '') = ''", sql)
        self.assertIn("'..'", sql)
        self.assertIn("CAST(ROUND(", sql)


class TestGrossPayMath(unittest.TestCase):
    def test_lindsay_regular_plus_ot_plus_perk(self):
        hours, ot, rate, ot_rate, perk = 77.88, 1.0, 25.0, 37.5, 20.0
        gross = (hours - ot) * rate + ot * ot_rate
        total = gross + perk
        self.assertAlmostEqual(gross, 1922.0 + 37.5, places=2)
        self.assertAlmostEqual(total, 1979.5, places=2)

    def test_adp_half_up_ximena_kayah(self):
        from decimal import Decimal, ROUND_HALF_UP

        def wages(hours: float, rate: float) -> float:
            return float(
                (Decimal(str(hours)) * Decimal(str(rate))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )

        self.assertEqual(wages(47.30, 15.25), 721.33)
        self.assertEqual(wages(43.98, 16.25), 714.68)
        self.assertEqual(round(47.30 * 15.25, 2), 721.32)


if __name__ == "__main__":
    unittest.main()
