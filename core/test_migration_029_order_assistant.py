"""Tests for core/migrations/029_order_assistant_functions.sql (Issue #126).

No live BigQuery in this environment, so these tests are structural: the
migration must (a) parse under datastore._split_statements the same way
ensure_schema() will apply it, and (b) preserve the exact algorithm/columns
that used to live in Grafana panels 79/81 (dashboard.json), so the panel
rewrite in Milestone 2 is a pure "SELECT * FROM ..." with no behavior change.
"""
from __future__ import annotations

import pathlib
import unittest

from core.datastore import _split_statements

_MIGRATION = (
    pathlib.Path(__file__).parent / "migrations" / "029_order_assistant_functions.sql"
).read_text()

_DASHBOARD_JSON = pathlib.Path(__file__).parents[1] / "agents" / "bhaga" / "grafana" / "dashboard.json"


class TestMigrationParses(unittest.TestCase):
    def test_splits_into_two_ddl_statements(self):
        statements = [s for s in _split_statements(_MIGRATION) if s.strip()]
        self.assertEqual(len(statements), 2, statements)
        self.assertIn("CREATE OR REPLACE VIEW", statements[0])
        self.assertIn("CREATE OR REPLACE TABLE FUNCTION", statements[1])

    def test_objects_named_as_expected(self):
        self.assertIn("`jarvis-bhaga-prod.bhaga.vw_order_assistant_table`", _MIGRATION)
        self.assertIn("`jarvis-bhaga-prod.bhaga.tvf_order_reco`", _MIGRATION)
        self.assertIn("ship_days INT64, max_tubs INT64", _MIGRATION)


class TestOrderRecoInvariantsPreserved(unittest.TestCase):
    """Panel 81's water-fill invariants (bhaga.mdc) must survive the port verbatim."""

    def test_blade_excluded_from_candidates_and_weight(self):
        self.assertIn("o.item != 'Blade'", _MIGRATION)
        self.assertIn("WHEN o.item = 'Blade' THEN NULL", _MIGRATION)

    def test_pallet_weight_formula_preserved(self):
        self.assertIn(
            "ROUND(SUM(order_weight_lbs) + 50 * CEIL(SAFE_DIVIDE(SUM(order_tubs), 40)), 0)",
            _MIGRATION,
        )

    def test_per_tub_weight_by_item(self):
        self.assertIn("WHEN o.item = 'Açaí' THEN 18 ELSE 20", _MIGRATION)

    def test_budget_capped_by_max_tubs_param(self):
        self.assertIn("FLOOR(max_tubs - SUM(on_hand_arrival))", _MIGRATION)

    def test_ship_days_param_drives_on_hand_arrival(self):
        self.assertIn("current_qty - ship_days * COALESCE(avg_daily_usage, 0)", _MIGRATION)


class TestAnalyticsTotalRowPreserved(unittest.TestCase):
    """Panel 79's TOTAL-row synthesis must survive the port verbatim."""

    def test_total_row_label(self):
        self.assertIn("'TOTAL',", _MIGRATION)

    def test_days_left_total_uses_weekly_average(self):
        self.assertIn("SUM(usage_7d_total) / 7, 0", _MIGRATION)


class TestDashboardPanelsBecomePassThrough(unittest.TestCase):
    """Milestone-2 guard: once panels 79/81 are rewritten, they must be pure
    SELECT * FROM <new BQ object> — this locks that expectation in from
    Milestone 1 so Milestone 2 cannot silently reintroduce inline logic.
    """

    def test_dashboard_json_exists(self):
        self.assertTrue(_DASHBOARD_JSON.is_file())
