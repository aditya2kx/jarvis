"""Tests for scripts/check_live_labor_cost.py (Issue #267 frozen labor $)."""
from __future__ import annotations

import check_live_labor_cost as gate


class TestFrozenLaborCostDetect:
    def test_flags_model_view_labor_dollars(self):
        sql = (
            "SELECT hourly_labor_cost FROM `jarvis-bhaga-prod.bhaga.vw_model_labor_daily`"
        )
        assert gate._frozen_labor_cost(sql)

    def test_allows_live_view(self):
        sql = "SELECT hourly_labor_cost FROM `bhaga.vw_labor_daily_live`"
        assert not gate._frozen_labor_cost(sql)

    def test_allows_sales_only_from_model(self):
        sql = "SELECT net_sales, orders FROM vw_model_labor_daily"
        assert not gate._frozen_labor_cost(sql)

    def test_grafana_labor_pct_must_use_live(self):
        frozen = "SELECT hourly_labor_cost FROM vw_model_labor_weekly"
        assert gate._grafana_labor_cost_not_live(frozen)
        live = "SELECT hourly_labor_cost FROM vw_labor_weekly_live"
        assert not gate._grafana_labor_cost_not_live(live)
