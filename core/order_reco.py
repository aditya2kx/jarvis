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

Migration 067: write-then-swap — INSERT a shared `refreshed_at` generation,
then DELETE rows whose `refreshed_at` differs. Avoids an empty/torn table
between DELETE and INSERT. `tvf_order_reco_slot_n` QUALIFYs `s_prev` to the
latest `refreshed_at` per item.

Issue #350: the whole recompute is one BigQuery stored procedure,
`sp_refresh_order_reco` (migration 081) — one job, all slots, atomic swap,
append-only history + a runs ledger, and superseded-on-conflict so concurrent
refreshes never tear the table. The TVF chain below survives only as the
`BHAGA_ORDER_RECO_LEGACY=1` rollback path.

Public API
----------
refresh_order_reco(store="palmetto", *, trigger="python", requested_by="bhaga") -> str | None
    CALLs sp_refresh_order_reco and waits for it; returns the run_id (None when
    BQ is disabled or the legacy path ran). Raises on failure after logging an
    `order_reco_failed run_id=...` breadcrumb.

Called from: nightly daily_refresh, deploy post-ensure_schema, and the console
order-reco-only job. cloud/webhook/handler.py duplicates the CALL inline —
keep both in sync.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

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


def refresh_order_reco(
    store: str = "palmetto", *, trigger: str = "python", requested_by: str = "bhaga",
) -> str | None:
    """Recompute inventory_order_reco for *store*. No-op when BQ is disabled."""
    if os.environ.get("BHAGA_ORDER_RECO_LEGACY") == "1":
        _refresh_legacy(store)
        return None
    from core.datastore import _param_config, fq, get_client

    client = get_client()
    if client is None:
        return None
    run_id = f"py-{uuid.uuid4().hex[:12]}"
    try:
        client.query(
            f"CALL {fq('sp_refresh_order_reco')}"
            "(@store, @trigger, @by, @run_id, FALSE, NULL, NULL, NULL)",
            job_config=_param_config([
                ("store", "STRING", store),
                ("trigger", "STRING", trigger),
                ("by", "STRING", requested_by),
                ("run_id", "STRING", run_id),
            ]),
        ).result()
    except Exception as exc:
        logger.error(
            "order_reco_failed run_id=%s store=%s trigger=%s exc=%s", run_id, store, trigger, exc,
        )
        raise
    logger.info("refresh_order_reco: committed store=%s run_id=%s trigger=%s", store, run_id, trigger)
    return run_id


def _refresh_legacy(store: str) -> None:
    """Pre-#350 TVF chain (write-then-swap, migration 067) — rollback path only."""
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

    if not slots:
        read_query(f"DELETE FROM {fq('inventory_order_reco')} WHERE store = '{store}'")
        logger.info("refresh_order_reco: no next dates store=%s — cleared", store)
        return

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    sel = _RECO_SELECT_FROM_TVF.replace("CURRENT_TIMESTAMP()", f"TIMESTAMP('{gen}')")

    # Slot 1 burns from current qty / today.
    read_query(
        f"INSERT INTO {fq('inventory_order_reco')} ({_RECO_INSERT_COLS})"
        f" SELECT '{store}', 1, {sel}"
        f" FROM {fq('tvf_order_reco_slot1')}({max_tubs})"
    )
    # Slots >= 2 chain from the prior materialized slot (migration 052).
    for slot in slots:
        if slot < 2:
            continue
        read_query(
            f"INSERT INTO {fq('inventory_order_reco')} ({_RECO_INSERT_COLS})"
            f" SELECT '{store}', {slot}, {sel}"
            f" FROM {fq('tvf_order_reco_slot_n')}({max_tubs}, {slot})"
        )
    read_query(
        f"DELETE FROM {fq('inventory_order_reco')} "
        f"WHERE store = '{store}' AND refreshed_at != TIMESTAMP('{gen}')"
    )
    logger.info(
        "refresh_order_reco: recomputed store=%s max_tubs=%d slots=%s",
        store,
        max_tubs,
        slots,
    )
