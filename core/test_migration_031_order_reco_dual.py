"""Tests for core/migrations/031_order_reco_dual.sql (Issue #137, Option D).

No live BigQuery in this environment, so these tests are structural: the
migration must (a) parse under datastore._split_statements the same way
ensure_schema() will apply it, and (b) preserve the exact algorithm/columns
from migration 029's tvf_order_reco per slot, plus the Option D materialized-
chaining fix (slot 2 reads slot 1 back from the table, never a nested TVF
call or a re-derived CTE — see the migration's module comment for why that
combination blew BigQuery's query-planning complexity limit in prod-live
testing).
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "031_order_reco_dual.sql"
).read_text()


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_expected_statements(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        # DROP FUNCTION, CREATE TABLE, next-dates VIEW, slot1 TVF, slot2 TVF,
        # slot1 VIEW, slot2 VIEW.
        self.assertEqual(len(statements), 7, statements)
        self.assertIn("DROP TABLE FUNCTION IF EXISTS", statements[0])
        self.assertIn("CREATE TABLE IF NOT EXISTS", statements[1])
        self.assertIn("inventory_order_reco", statements[1])
        self.assertIn("CREATE OR REPLACE VIEW", statements[2])
        self.assertIn("vw_order_reco_next_dates", statements[2])
        self.assertIn("CREATE OR REPLACE TABLE FUNCTION", statements[3])
        self.assertIn("tvf_order_reco_slot1", statements[3])
        self.assertIn("CREATE OR REPLACE TABLE FUNCTION", statements[4])
        self.assertIn("tvf_order_reco_slot2", statements[4])
        self.assertIn("vw_order_reco_slot1", statements[5])
        self.assertIn("vw_order_reco_slot2", statements[6])

    def test_objects_named_as_expected(self):
        for obj in (
            "`jarvis-bhaga-prod.bhaga.vw_order_reco_next_dates`",
            "`jarvis-bhaga-prod.bhaga.inventory_order_reco`",
            "`jarvis-bhaga-prod.bhaga.tvf_order_reco_slot1`",
            "`jarvis-bhaga-prod.bhaga.tvf_order_reco_slot2`",
            "`jarvis-bhaga-prod.bhaga.vw_order_reco_slot1`",
            "`jarvis-bhaga-prod.bhaga.vw_order_reco_slot2`",
        ):
            self.assertIn(obj, _MIGRATION)


class TestNextDatesView(unittest.TestCase):
    def test_filters_to_future_dates_only(self):
        self.assertIn("delivery_date >= CURRENT_DATE('America/Chicago')", _MIGRATION)

    def test_caps_at_two_slots_oldest_first(self):
        self.assertIn("ROW_NUMBER() OVER (ORDER BY delivery_date) AS slot", _MIGRATION)
        self.assertIn("WHERE slot <= 2", _MIGRATION)


class TestMaterializedChaining(unittest.TestCase):
    """The Option D fix: slot 2 must read slot 1 back from the materialized
    table, never call slot 1's TVF or re-derive its CTEs in the same query
    (that combination is what blew the query-planning complexity limit)."""

    def test_slot2_reads_slot1_from_materialized_table_not_nested_tvf_call(self):
        self.assertIn("FROM `jarvis-bhaga-prod.bhaga.inventory_order_reco`", _MIGRATION)
        self.assertIn("WHERE store = 'palmetto' AND Slot = 1 AND Item != 'TOTAL'", _MIGRATION)
        # Never call tvf_order_reco_slot1(...) from inside slot 2's body.
        slot2_start = _MIGRATION.index("CREATE OR REPLACE TABLE FUNCTION `jarvis-bhaga-prod.bhaga.tvf_order_reco_slot2`")
        slot2_body = _MIGRATION[slot2_start:]
        self.assertNotIn("tvf_order_reco_slot1`(", slot2_body)

    def test_slot2_ship_days_derived_from_real_calendar_gap(self):
        self.assertIn("DATE_DIFF(dd.d2, dd.d1, DAY)", _MIGRATION)

    def test_drops_failed_flat_dual_tvf_experiment(self):
        self.assertIn("DROP TABLE FUNCTION IF EXISTS `jarvis-bhaga-prod.bhaga.tvf_order_reco_dual`", _MIGRATION)


class TestOrderRecoInvariantsPreservedPerSlot(unittest.TestCase):
    """Panel 81's water-fill invariants (bhaga.mdc) must survive per-slot, verbatim."""

    def test_blade_excluded_from_candidates_and_weight(self):
        self.assertEqual(_MIGRATION.count("o.item != 'Blade'"), 2)
        self.assertEqual(_MIGRATION.count("WHEN o.item = 'Blade' THEN NULL"), 2)

    def test_pallet_weight_formula_preserved_both_slots(self):
        formula = "ROUND(SUM(order_weight_lbs) + 50 * CEIL(SAFE_DIVIDE(SUM(order_tubs), 40)), 0)"
        self.assertEqual(_MIGRATION.count(formula), 2)

    def test_per_tub_weight_by_item_both_slots(self):
        self.assertEqual(_MIGRATION.count("WHEN o.item = 'Açaí' THEN 18 ELSE 20"), 2)

    def test_budget_capped_by_max_tubs_param_both_slots(self):
        self.assertEqual(_MIGRATION.count("FLOOR(max_tubs - SUM(on_hand_arrival))"), 2)

    def test_estimated_vs_actual_override_present_both_slots(self):
        self.assertEqual(_MIGRATION.count("WHEN h.is_actual THEN COALESCE(a.actual_tubs, 0)"), 2)


class TestEmptyOnNoSecondDate(unittest.TestCase):
    def test_slot2_guards_on_second_date_presence(self):
        self.assertIn("WHERE o.store = 'palmetto' AND dd.d2 IS NOT NULL", _MIGRATION)
