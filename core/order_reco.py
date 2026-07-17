"""Recompute the materialized dual-date Order Recommendation (Issue #137).

See core/migrations/031_order_reco_dual.sql for why this is a materialized
table (Option D) instead of a live chained TVF: a single query that computes
both restock slots blows BigQuery's query-planning complexity limit, so each
slot is computed by a SEPARATE table function call and the results are
written into `inventory_order_reco`. Slot 2's TVF reads slot 1's row back
from that table, so slot 1 MUST be inserted before slot 2's INSERT runs.

Migration 041 adds `delivery_date` on each row so the console combined view
can join by calendar date (not Slot alone). INSERTs must list columns
explicitly — `SELECT store, slot, t.*, ts` mis-maps after ALTER ADD.

Public API
----------
refresh_order_reco(store="palmetto") -> None
    DELETE-then-INSERT (idempotent) inventory_order_reco for *store*: reads
    `order_reco_max_tubs` from store_config (default 120), then runs slot 1's
    TVF and inserts its rows, then runs slot 2's TVF (which reads slot 1's
    just-inserted rows) and inserts its rows.

Called from: nightly daily_refresh, restock submit, config-set on
order_reco_max_tubs, deploy post-ensure_schema, and console stale-refresh.
cloud/webhook/handler.py duplicates the SQL inline — keep both in sync.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TUBS = 120

# Explicit column list — must match inventory_order_reco + TVF output (041).
_RECO_INSERT_COLS = (
    "store, Slot, Item, `Current Qty`, `Avg per day`, `On Hand at Restock`, "
    "`Order Tubs`, `Order Weight lbs`, `After Restock`, `Days Left After Restock`, "
    "_ord, refreshed_at, delivery_date"
)
_RECO_SELECT_FROM_TVF = (
    "Item, `Current Qty`, `Avg per day`, `On Hand at Restock`, "
    "`Order Tubs`, `Order Weight lbs`, `After Restock`, `Days Left After Restock`, "
    "_ord, CURRENT_TIMESTAMP(), delivery_date"
)


def refresh_order_reco(store: str = "palmetto") -> None:
    """Recompute inventory_order_reco for *store*. No-op when BQ is disabled."""
    from core.datastore import fq, read_query
    from core.store_config import get_config

    max_tubs_str = get_config(store, "order_reco_max_tubs")
    max_tubs = int(max_tubs_str) if max_tubs_str else _DEFAULT_MAX_TUBS

    read_query(f"DELETE FROM {fq('inventory_order_reco')} WHERE store = '{store}'")
    read_query(
        f"INSERT INTO {fq('inventory_order_reco')} ({_RECO_INSERT_COLS})"
        f" SELECT '{store}', 1, {_RECO_SELECT_FROM_TVF}"
        f" FROM {fq('tvf_order_reco_slot1')}({max_tubs})"
    )
    # Slot 2 must run AFTER slot 1's INSERT lands — its TVF reads slot 1's
    # row back from inventory_order_reco (see migration 031 module comment).
    read_query(
        f"INSERT INTO {fq('inventory_order_reco')} ({_RECO_INSERT_COLS})"
        f" SELECT '{store}', 2, {_RECO_SELECT_FROM_TVF}"
        f" FROM {fq('tvf_order_reco_slot2')}({max_tubs})"
    )
    logger.info("refresh_order_reco: recomputed store=%s max_tubs=%d", store, max_tubs)
