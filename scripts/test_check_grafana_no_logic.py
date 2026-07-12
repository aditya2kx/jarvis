"""Tests for scripts/check_grafana_no_logic.py (Issue #126)."""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

import check_grafana_no_logic as gate


def _dashboard(panels: list[dict]) -> dict:
    return {"panels": panels}


def _panel(panel_id: int, raw_sql: str, ptype: str = "table") -> dict:
    return {
        "id": panel_id,
        "type": ptype,
        "title": f"panel {panel_id}",
        "targets": [{"refId": "A", "rawSql": raw_sql}],
    }


class TestPassThroughAllowed(unittest.TestCase):
    def test_simple_view_select_is_clean(self):
        sql = "SELECT * FROM `jarvis-bhaga-prod.bhaga.vw_order_assistant_table`"
        self.assertEqual(gate._violations(sql), [])

    def test_tvf_with_variables_is_clean(self):
        sql = "SELECT * FROM `jarvis-bhaga-prod.bhaga.tvf_order_reco`($oa_ship_days, $oa_max_tubs)"
        self.assertEqual(gate._violations(sql), [])

    def test_where_and_order_by_allowed(self):
        sql = (
            "SELECT date, orders FROM `jarvis-bhaga-prod.bhaga.vw_model_labor_daily` "
            "WHERE date >= DATE('$date_from') ORDER BY date"
        )
        self.assertEqual(gate._violations(sql), [])


class TestBannedConstructsDetected(unittest.TestCase):
    def test_cte_is_flagged(self):
        sql = "WITH x AS (SELECT 1) SELECT * FROM x"
        self.assertTrue(any("CTE" in v for v in gate._violations(sql)))

    def test_union_is_flagged(self):
        sql = (
            "SELECT a FROM `bhaga.vw_x` UNION ALL SELECT b FROM `bhaga.vw_y`"
        )
        self.assertTrue(any("UNION" in v for v in gate._violations(sql)))

    def test_join_is_flagged(self):
        sql = (
            "SELECT * FROM `bhaga.model_forecast_daily` f "
            "LEFT JOIN `bhaga.vw_model_labor_daily` a ON a.date = f.date"
        )
        self.assertTrue(any("JOIN" in v for v in gate._violations(sql)))

    def test_correlated_subquery_is_flagged(self):
        sql = (
            "SELECT o.x, (SELECT STRING_AGG(s.employee) FROM `bhaga.vw_staff_on_shift` s) "
            "FROM `bhaga.vw_kds_order_investigation` o"
        )
        self.assertTrue(any("subquery" in v for v in gate._violations(sql)))

    def test_generate_array_is_flagged(self):
        sql = (
            "SELECT * FROM `bhaga.vw_x` CROSS JOIN UNNEST(GENERATE_ARRAY(1, 10)) AS k"
        )
        violations = gate._violations(sql)
        self.assertTrue(any("GENERATE_ARRAY" in v for v in violations))

    def test_non_view_from_is_flagged(self):
        sql = "SELECT * FROM `bhaga.model_forecast_daily`"
        self.assertTrue(any("FROM clause" in v for v in gate._violations(sql)))


class TestMainExitCodes(unittest.TestCase):
    def _write_dashboard(self, panels: list[dict]) -> pathlib.Path:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir=tempfile.gettempdir()
        )
        json.dump(_dashboard(panels), tmp)
        tmp.close()
        return pathlib.Path(tmp.name)

    def test_clean_panels_exit_zero(self):
        path = self._write_dashboard([
            _panel(79, "SELECT * FROM `bhaga.vw_order_assistant_table`"),
            _panel(83, "SELECT * FROM `bhaga.vw_order_reco_combined`"),
        ])
        dashboard = json.loads(path.read_text())
        hard_failures = []
        for panel_id, title, sql in gate._iter_data_panels(dashboard):
            violations = gate._violations(sql)
            if violations and panel_id not in gate.WAIVED_PANELS:
                hard_failures.append(panel_id)
        self.assertEqual(hard_failures, [])
        path.unlink()

    def test_must_be_clean_panel_with_logic_fails(self):
        dashboard = _dashboard([_panel(83, "WITH x AS (SELECT 1) SELECT * FROM x")])
        violations = gate._violations(dashboard["panels"][0]["targets"][0]["rawSql"])
        self.assertTrue(violations)
        self.assertIn(83, gate.MUST_BE_CLEAN)

    def test_unwaived_logic_panel_fails(self):
        sql = "WITH x AS (SELECT 1) SELECT * FROM x"
        violations = gate._violations(sql)
        self.assertTrue(violations)
        self.assertNotIn(9999, gate.WAIVED_PANELS)

    def test_row_and_text_panels_are_skipped(self):
        dashboard = _dashboard([
            {"id": 77, "type": "row", "title": "8. Order Assistant", "panels": []},
            {"id": 80, "type": "text", "title": "Methodology", "options": {}},
        ])
        self.assertEqual(list(gate._iter_data_panels(dashboard)), [])

    def test_real_dashboard_passes_gate(self):
        """Integration: the actual repo dashboard.json must pass the gate."""
        dashboard = json.loads(gate._DEFAULT_DASHBOARD.read_text())
        hard_failures = []
        for panel_id, title, sql in gate._iter_data_panels(dashboard):
            violations = gate._violations(sql)
            if not violations:
                continue
            if panel_id in gate.MUST_BE_CLEAN:
                hard_failures.append((panel_id, "must-be-clean", violations))
            elif panel_id not in gate.WAIVED_PANELS:
                hard_failures.append((panel_id, "unwaived", violations))
        self.assertEqual(hard_failures, [])


if __name__ == "__main__":
    unittest.main()
