"""Tests for scripts/check_append_only_history.py (Issue #350)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_append_only_history as gate  # noqa: E402

P = pathlib.Path("x.sql")


def test_repo_is_clean():
    assert gate.main() == 0


def test_flags_delete_update_merge_truncate_drop():
    for sql in (
        "DELETE FROM `p.bhaga.inventory_order_reco_history` WHERE TRUE",
        "UPDATE bhaga.inventory_edit_log SET edited_by = 'x' WHERE TRUE",
        "MERGE `p.bhaga.inventory_order_reco_runs` T USING s ON FALSE",
        "TRUNCATE TABLE bhaga.inventory_order_reco_history",
        "DROP TABLE IF EXISTS `p.bhaga.inventory_edit_log`",
    ):
        assert gate.violations(P, sql), sql


def test_allows_inserts_reads_and_the_view():
    ok = (
        "INSERT INTO `p.bhaga.inventory_order_reco_history` SELECT * FROM _reco;\n"
        "SELECT * FROM bhaga.inventory_edit_log;\n"
        "DELETE FROM `p.bhaga.inventory_order_reco` WHERE store = 'x';\n"
        "CREATE OR REPLACE VIEW bhaga.vw_inventory_edit_log AS SELECT 1;\n"
        "DROP VIEW IF EXISTS bhaga.vw_inventory_edit_log;\n"
    )
    assert gate.violations(P, ok) == []


def test_flags_partition_expiry():
    sql = "CREATE TABLE bhaga.inventory_edit_log (x INT64) OPTIONS (partition_expiration_days = 30)"
    assert gate.violations(P, sql)
