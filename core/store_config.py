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

delete_config(store, key) -> None
    Delete all store_config rows for the given (store, key) pair.
    Use only for derived keys that should never be stored.

resolve_data_window_end(store) -> str | None
    Return the last complete date as ISO string, derived live from
    MAX(square_transactions.date_local).  Never reads store_config —
    data_window_end is a derived value, not a human tunable.
"""

from __future__ import annotations

import datetime
import logging

logger = logging.getLogger(__name__)

_TABLE = "store_config"

# Keys that are DERIVED from BQ raw data and must never be stored in
# store_config.  set_config() raises ValueError for any key in this set so
# no future code path can accidentally freeze a derived value.
_DERIVED_KEYS: frozenset[str] = frozenset({"data_window_end"})


def get_config(store: str, key: str) -> str | None:
    """Return the latest value for *key* in *store*, or None if absent."""
    from core.datastore import fq, read_query

    rows = read_query(
        f"SELECT value FROM {fq(_TABLE)}"
        f" WHERE store = '{store}' AND key = '{key}'"
        f" ORDER BY updated_at DESC LIMIT 1"
    )
    if rows:
        return rows[0]["value"]
    return None


def get_all(store: str) -> dict[str, str]:
    """Return all (key, value) pairs for *store* (latest value per key)."""
    from core.datastore import fq, read_query

    rows = read_query(
        f"SELECT key, value FROM ("
        f"  SELECT key, value, ROW_NUMBER() OVER (PARTITION BY key ORDER BY updated_at DESC) AS rn"
        f"  FROM {fq(_TABLE)}"
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
    Raises ValueError for keys in _DERIVED_KEYS (e.g. data_window_end) —
    those are derived from BQ raw data and must never be stored.
    """
    if key in _DERIVED_KEYS:
        raise ValueError(
            f"store_config.set_config: '{key}' is a derived key and must not be "
            f"stored in store_config.  Use resolve_data_window_end() to read it."
        )
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


def delete_config(store: str, key: str) -> None:
    """Delete all store_config rows for (store, key).

    Use to remove stale or erroneously-stored entries.  For derived keys
    (e.g. data_window_end) this is the cleanup path after an old migrate run
    wrote a value that should never have been stored.
    """
    from core.datastore import fq, read_query

    read_query(
        f"DELETE FROM {fq(_TABLE)}"
        f" WHERE store = '{store}' AND key = '{key}'"
    )
    logger.info("store_config: deleted %s/%s", store, key)


def resolve_data_window_end(store: str) -> str | None:  # noqa: ARG001
    """Return the last complete business date as an ISO string.

    Derived live from MAX(square_transactions.date_local) in BigQuery.
    Never reads store_config — data_window_end is a derived value, not a
    human tunable, and must not be cached in store_config (a stale stored
    value would freeze the review crediting window, the 2026-06-15 incident).

    Returns None when BQ is unavailable or square_transactions is empty.
    The ``store`` argument is accepted for API symmetry but is not used —
    the BQ dataset is already scoped to the single prod store.
    """
    from core.datastore import fq, read_query

    try:
        rows = read_query(
            f"SELECT CAST(MAX(date_local) AS STRING) AS m"
            f" FROM {fq('square_transactions')}"
        )
        return (rows[0]["m"] if rows else None) or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve_data_window_end: BQ query failed: %s", exc)
        return None
