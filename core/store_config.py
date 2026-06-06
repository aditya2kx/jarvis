"""Operator-editable config/tunables stored in `bhaga.store_config`.

Replaces the Sheet config tab as the authoritative read source.  The Sheet
config tab continues to be *written* by build_config_rows (as a read-only
projection for human inspection), but all pipeline reads MUST come through
here.

Public API
----------
get_config(store, key) -> str | None
    Return the most-recently-updated value for the given (store, key) pair, or
    None if no row exists.

get_all(store) -> dict[str, str]
    Return all keys for *store* as a mapping.

set_config(store, key, value, *, updated_by, notes="") -> None
    Upsert the (store, key) row with the new value, updated_at=now, and
    updated_by.  Idempotent — safe to call repeatedly.
"""

from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

_TABLE = "store_config"


def get_config(store: str, key: str) -> str | None:
    """Return the latest value for *key* in *store*, or None if absent."""
    from core.datastore import read_query

    rows = read_query(
        f"SELECT value FROM `jarvis-bhaga-prod.bhaga.{_TABLE}`"
        f" WHERE store = '{store}' AND key = '{key}'"
        f" ORDER BY updated_at DESC LIMIT 1"
    )
    if rows:
        return rows[0]["value"]
    return None


def get_all(store: str) -> dict[str, str]:
    """Return all (key, value) pairs for *store* (latest value per key)."""
    from core.datastore import read_query

    rows = read_query(
        f"SELECT key, value FROM ("
        f"  SELECT key, value, ROW_NUMBER() OVER (PARTITION BY key ORDER BY updated_at DESC) AS rn"
        f"  FROM `jarvis-bhaga-prod.bhaga.{_TABLE}`"
        f"  WHERE store = '{store}'"
        f") WHERE rn = 1"
    )
    return {r["key"]: r["value"] for r in rows}


def set_config(
    store: str,
    key: str,
    value: str,
    *,
    updated_by: str,
    notes: str = "",
) -> None:
    """Upsert a single (store, key) config row.

    Uses a MERGE so repeated calls are idempotent — the last write wins.
    """
    from core.datastore import load_rows

    row = {
        "store":      store,
        "key":        key,
        "value":      value,
        "notes":      notes or None,
        "updated_at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    load_rows(
        _TABLE,
        [row],
        merge_keys=["store", "key"],
        column_bq_types={"updated_at": "TIMESTAMP"},
    )
    logger.info("store_config: set %s/%s = %r (by %s)", store, key, value, updated_by)
