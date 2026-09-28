"""Recompute the materialized dual-date Order Recommendation (Issue #137).

The recompute is one BigQuery stored procedure, `sp_refresh_order_reco`
(migration 081, Issue #350): one job, all planning slots, an atomic swap of
`inventory_order_reco`, an append-only `inventory_order_reco_history`
generation + `inventory_order_reco_runs` ledger row, and superseded-on-conflict
so concurrent refreshes never tear the table. The per-slot table functions
(migrations 031/041/052/067) remain in BigQuery for the sandbox parity harness
(`scripts/order_reco_sandbox.py`) but nothing in the runtime calls them.

Public API
----------
refresh_order_reco(store="palmetto", *, trigger="python", requested_by="bhaga") -> str | None
    CALLs sp_refresh_order_reco and waits for it; returns the run_id (None when
    BQ is disabled). Raises on failure after logging an
    `order_reco_failed run_id=...` breadcrumb.

Called from: nightly daily_refresh and deploy post-ensure_schema.
cloud/webhook/handler.py duplicates the CALL inline — keep both in sync.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


def refresh_order_reco(
    store: str = "palmetto", *, trigger: str = "python", requested_by: str = "bhaga",
) -> str | None:
    """Recompute inventory_order_reco for *store*. No-op when BQ is disabled."""
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
