"""Recompute the materialized dual-date Order Recommendation (Issue #137).

See core/migrations/031_order_reco_dual.sql for why this is a materialized
table (Option D) instead of a live chained TVF: a single query that computes
both restock slots blows BigQuery's query-planning complexity limit, so each
slot is computed by a SEPARATE table function call and the results are
written into `inventory_order_reco`. Later slots read the prior slot's row
back from that table, so slot N-1 MUST be inserted before slot N runs.

Migration 041 adds `delivery_date` on each row so the console combined view
can join by calendar date (not Slot alone). INSERTs must list columns
explicitly — `SELECT store, slot, t.*, ts` mis-maps after ALTER ADD.

Migration 052: more than 2 planning slots via `tvf_order_reco_slot_n` and a
config-capped `vw_order_reco_next_dates` (order_reco_max_slots, default 4).

Public API
----------
refresh_order_reco(store="palmetto") -> None
    DELETE-then-INSERT (idempotent) inventory_order_reco for *store*: reads
    `order_reco_max_tubs` from store_config (default 120), then runs slot 1's
    TVF and inserts its rows, then runs slot_n for each live slot >= 2.

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

    slots = [
        int(r["slot"])
        for r in read_query(
            f"SELECT slot FROM {fq('vw_order_reco_next_dates')} ORDER BY slot"
        )
    ]

    read_query(f"DELETE FROM {fq('inventory_order_reco')} WHERE store = '{store}'")
    if not slots:
        logger.info("refresh_order_reco: no next dates store=%s — cleared", store)
        return

    # Slot 1 burns from current qty / today.
    read_query(
        f"INSERT INTO {fq('inventory_order_reco')} ({_RECO_INSERT_COLS})"
        f" SELECT '{store}', 1, {_RECO_SELECT_FROM_TVF}"
        f" FROM {fq('tvf_order_reco_slot1')}({max_tubs})"
    )
    # Slots >= 2 chain from the prior materialized slot (migration 052).
    for slot in slots:
        if slot < 2:
            continue
        read_query(
            f"INSERT INTO {fq('inventory_order_reco')} ({_RECO_INSERT_COLS})"
            f" SELECT '{store}', {slot}, {_RECO_SELECT_FROM_TVF}"
            f" FROM {fq('tvf_order_reco_slot_n')}({max_tubs}, {slot})"
        )
    logger.info(
        "refresh_order_reco: recomputed store=%s max_tubs=%d slots=%s",
        store,
        max_tubs,
        slots,
    )
