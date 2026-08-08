"""Fulfillment / ops-clock helpers for Square Orders.

Square ``created_at`` is place-time (often overnight for SCHEDULED 3P orders).
Kitchen/demand hour charts need the promised fulfillment slot instead:

    ops_at = COALESCE(pickup_at, deliver_at, courier_pickup_at, ready_at,
                      closed_at, created_at)

All raw Square timestamps are stored as UTC ISO strings (``*_utc``). Derived
ops clock is stored in the shop timezone (America/Chicago) to match
``created_at_local_iso`` / ``date_local``.
"""

from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo

# Columns written onto square_transactions (and passed through map_square_transaction).
FULFILLMENT_BQ_FIELDS: tuple[str, ...] = (
    "fulfillment_type",
    "schedule_type",
    "pickup_at_utc",
    "deliver_at_utc",
    "courier_pickup_at_utc",
    "ready_at_utc",
    "picked_up_at_utc",
    "delivered_at_utc",
    "placed_at_utc",
    "accepted_at_utc",
    "closed_at_utc",
    "ops_at_local_iso",
    "ops_date_local",
    "ops_hour_local",
)


def _normalize_rfc3339(ts: str | None) -> str | None:
    if not ts or not str(ts).strip():
        return None
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Keep a stable UTC ISO form (Square sometimes emits fractional seconds).
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(ts: str | None) -> datetime.datetime | None:
    norm = _normalize_rfc3339(ts)
    if not norm:
        return None
    s = norm[:-1] + "+00:00" if norm.endswith("Z") else norm
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _details(fulfillment: dict[str, Any]) -> dict[str, Any]:
    ftype = (fulfillment.get("type") or "").upper()
    if ftype == "PICKUP":
        return fulfillment.get("pickup_details") or {}
    if ftype == "DELIVERY":
        return fulfillment.get("delivery_details") or {}
    # SHIPMENT etc. — best-effort
    return (
        fulfillment.get("pickup_details")
        or fulfillment.get("delivery_details")
        or fulfillment.get("shipment_details")
        or {}
    )


def extract_fulfillment_fields(
    order: dict[str, Any] | None,
    *,
    shop_tz: str = "America/Chicago",
) -> dict[str, Any]:
    """Pull fulfillment timestamps + derived ops clock from a Square Order.

    Returns a dict with all ``FULFILLMENT_BQ_FIELDS`` keys. Missing values are
    ``None`` (BQ NULL) except string fields which may be ``""`` for empty type.
    When ``order`` is None / has no useful timestamps, ops falls back to None
    (caller should leave existing place-time as the Hour fallback).
    """
    empty: dict[str, Any] = {k: None for k in FULFILLMENT_BQ_FIELDS}
    if not order:
        return empty

    fulfillments = order.get("fulfillments") or []
    # Prefer the first non-canceled fulfillment; else first.
    chosen: dict[str, Any] | None = None
    for f in fulfillments:
        if (f.get("state") or "").upper() == "CANCELED":
            continue
        chosen = f
        break
    if chosen is None and fulfillments:
        chosen = fulfillments[0]

    ftype = ""
    schedule_type = None
    pickup_at = deliver_at = courier_pickup_at = None
    ready_at = picked_up_at = delivered_at = None
    placed_at = accepted_at = None

    if chosen:
        ftype = (chosen.get("type") or "").upper()
        d = _details(chosen)
        schedule_type = d.get("schedule_type") or None
        pickup_at = _normalize_rfc3339(d.get("pickup_at"))
        deliver_at = _normalize_rfc3339(d.get("deliver_at"))
        courier_pickup_at = _normalize_rfc3339(d.get("courier_pickup_at"))
        ready_at = _normalize_rfc3339(d.get("ready_at"))
        picked_up_at = _normalize_rfc3339(d.get("picked_up_at"))
        delivered_at = _normalize_rfc3339(d.get("delivered_at"))
        placed_at = _normalize_rfc3339(d.get("placed_at"))
        accepted_at = _normalize_rfc3339(d.get("accepted_at"))

    closed_at = _normalize_rfc3339(order.get("closed_at"))
    created_at = _normalize_rfc3339(order.get("created_at"))

    # Ops clock: promised slot first, then kitchen ready, then close/place.
    ops_src = next(
        (
            t
            for t in (
                pickup_at,
                deliver_at,
                courier_pickup_at,
                ready_at,
                closed_at,
                created_at,
            )
            if t
        ),
        None,
    )

    ops_at_local_iso = None
    ops_date_local = None
    ops_hour_local = None
    if ops_src:
        dt = _parse_utc(ops_src)
        if dt is not None:
            local = dt.astimezone(ZoneInfo(shop_tz))
            ops_at_local_iso = local.isoformat()
            ops_date_local = local.date().isoformat()
            ops_hour_local = local.hour

    return {
        "fulfillment_type": ftype or None,
        "schedule_type": schedule_type,
        "pickup_at_utc": pickup_at,
        "deliver_at_utc": deliver_at,
        "courier_pickup_at_utc": courier_pickup_at,
        "ready_at_utc": ready_at,
        "picked_up_at_utc": picked_up_at,
        "delivered_at_utc": delivered_at,
        "placed_at_utc": placed_at,
        "accepted_at_utc": accepted_at,
        "closed_at_utc": closed_at,
        "ops_at_local_iso": ops_at_local_iso,
        "ops_date_local": ops_date_local,
        "ops_hour_local": ops_hour_local,
    }


def enrich_transaction_record(
    rec: dict[str, Any],
    order: dict[str, Any] | None,
    *,
    shop_tz: str = "America/Chicago",
) -> dict[str, Any]:
    """Mutate ``rec`` with fulfillment/ops fields; return it."""
    fields = extract_fulfillment_fields(order, shop_tz=shop_tz)
    # If order missing, still derive ops from the place-time already on the row
    # so Hour Aggregation never sees NULL ops for plain Register payments.
    if fields.get("ops_at_local_iso") is None:
        local_iso = rec.get("created_at_local_iso") or ""
        if local_iso:
            try:
                dt = datetime.datetime.fromisoformat(local_iso)
                fields["ops_at_local_iso"] = dt.isoformat()
                fields["ops_date_local"] = dt.date().isoformat()
                fields["ops_hour_local"] = dt.hour
            except ValueError:
                pass
    rec.update(fields)
    return rec
