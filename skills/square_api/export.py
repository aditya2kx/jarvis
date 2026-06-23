#!/usr/bin/env python3
"""skills/square_api/export — Payments + Orders API -> synthesized CSVs.

Produces ``transactions-{start}-{end+1}.csv`` and ``items-{start}-{end+1}.csv``
in ``extracted/downloads/`` with the EXACT dashboard-export column layout that
``skills/square_tips/transactions_backend.parse_csv`` / ``parse_item_sales_csv``
consume. Because the files are byte-compatible with the scrape output, the whole
downstream (parse -> map -> BigQuery) is unchanged.

Timezone discipline (parity-critical): the dashboard CSV emits Date/Time in the
Square account *display* timezone (Eastern for Palmetto) with a
``Time Zone`` label, and the parser converts to the shop timezone. We mirror
that exactly — convert each Square ``created_at`` (UTC) to the account display
TZ and write the matching label — so ``created_at_src_iso`` (Eastern) and the
derived ``date_local`` (Central) match the scrape byte-for-byte.

Money discipline: amounts are written as ``$X.YZ`` / ``-$X.YZ`` exactly as
``parse_money_cents`` expects.
"""

from __future__ import annotations

import csv
import datetime
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import project_dir
from skills.square_api.client import SquareClient

DOWNLOADS_DIR = pathlib.Path(project_dir()) / "extracted" / "downloads"

# 55-column Transactions export header (verbatim dashboard export order).
TXN_HEADER = [
    "Date", "Time", "Time Zone", "Gross Sales", "Discounts",
    "Service Charges", "Net Sales", "Gift Card Sales", "Tax", "Tip",
    "Partial Refunds", "Total Collected", "Source", "Card",
    "Card Entry Methods", "Cash", "Square Gift Card", "Other Tender",
    "Other Tender Type", "Tender Note", "Fees", "Net Total",
    "Transaction ID", "Payment ID", "Card Brand", "PAN Suffix",
    "Device Name", "Staff Name", "Staff ID", "Details", "Description",
    "Event Type", "Location", "Dining Option", "Customer ID",
    "Customer Name", "Customer Reference ID", "Device Nickname",
    "Third Party Fees", "Deposit ID", "Deposit Date", "Deposit Details",
    "Fee Percentage Rate", "Fee Fixed Rate", "Refund Reason",
    "Discount Name", "Transaction Status", "Cash App",
    "Order Reference ID", "Fulfillment Note", "Free Processing Applied",
    "Channel", "Unattributed Tips", "Table Info", "International Fee",
]

# 31-column Item Sales Detail header. Names at the indexes the parser reads
# (_ITEM_COL in transactions_backend: 0-5, 9-14, 15, 18-20, 24, 25, 28, 30)
# are real; the rest are placeholders the parser ignores by position.
ITEM_HEADER = [
    "Date", "Time", "Time Zone", "Category", "Item", "Qty",
    "Price Point Name", "SKU", "Modifiers Applied", "Gross Sales",
    "Discounts", "Net Sales", "Tax", "Transaction ID", "Payment ID",
    "Device Name", "Notes", "Details", "Event Type", "Location",
    "Dining Option", "Customer ID", "Customer Name",
    "Customer Reference ID", "Unit", "Count", "Itemization Type",
    "Commission", "Employee", "Token", "Channel",
]

DEFAULT_DISPLAY_TZ = "America/Chicago"


def _money(cents: int) -> str:
    """Render integer cents as a Square money string ('$1.50', '-$0.36', '$0.00')."""
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:.2f}"


def _money_to_cents(money: dict | None) -> int:
    """Square Money object -> integer cents (amount is already minor units)."""
    if not money:
        return 0
    return int(money.get("amount", 0) or 0)


def _rfc3339_day_bounds(start: datetime.date, end: datetime.date, display_tz: str):
    """Return (begin_time, end_time) in RFC3339 UTC covering [start, end] inclusive
    in the account display timezone (so the window matches what the dashboard
    date-range picker selects)."""
    tz = ZoneInfo(display_tz)
    begin_local = datetime.datetime.combine(start, datetime.time(0, 0, 0), tzinfo=tz)
    # end inclusive -> up to the start of (end + 1 day)
    end_local = datetime.datetime.combine(
        end + datetime.timedelta(days=1), datetime.time(0, 0, 0), tzinfo=tz
    )
    to_utc = lambda d: d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return to_utc(begin_local), to_utc(end_local)


def _created_at_to_display(created_at: str, display_tz: str) -> tuple[str, str]:
    """RFC3339 UTC timestamp -> (Date 'YYYY-MM-DD', Time 'HH:MM:SS') in display TZ."""
    s = created_at.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    local = dt.astimezone(ZoneInfo(display_tz))
    return local.date().isoformat(), local.strftime("%H:%M:%S")


def _tz_label(display_tz: str) -> str:
    """IANA -> Square's human display label (the parser maps it back)."""
    return {
        "America/New_York": "Eastern Time (US & Canada)",
        "America/Chicago": "Central Time (US & Canada)",
        "America/Denver": "Mountain Time (US & Canada)",
        "America/Los_Angeles": "Pacific Time (US & Canada)",
    }.get(display_tz, "Eastern Time (US & Canada)")


# ── reference data ────────────────────────────────────────────────


def _resolve_location(client: SquareClient, profile: dict) -> tuple[str, str]:
    """Return (location_id, location_name). Prefer the store profile; else the
    first active location from the API."""
    sq = profile.get("square", {})
    loc_id = sq.get("location_id")
    locations = client.get("/v2/locations").get("locations", []) or []
    by_id = {l.get("id"): l.get("name", "") for l in locations}
    if loc_id and loc_id in by_id:
        return loc_id, by_id[loc_id]
    if locations:
        return locations[0].get("id"), locations[0].get("name", "")
    raise RuntimeError("No Square locations available for this account.")


def _team_member_map(client: SquareClient) -> dict[str, str]:
    """team_member_id -> display name ('First Last')."""
    out: dict[str, str] = {}
    members = client.post_paginated(
        "/v2/team-members/search", body={"query": {}}, items_key="team_members"
    )
    for m in members:
        name = " ".join(p for p in [m.get("given_name"), m.get("family_name")] if p).strip()
        if m.get("id"):
            out[m["id"]] = name
    return out


def _category_map(client: SquareClient, orders: list[dict]) -> dict[str, str]:
    """catalog_object_id (item variation) -> category name.

    Two-step lookup:
    1. batch-retrieve ITEM_VARIATIONs with related objects → get parent ITEM
       objects; extract each ITEM's reporting_category (primary) or first
       category ID.
    2. batch-retrieve those CATEGORY objects to get their names.

    Square's batch-retrieve only returns one level of related objects, so
    the CATEGORY objects must be fetched in a separate round-trip.
    """
    var_ids = sorted({
        li.get("catalog_object_id")
        for o in orders for li in (o.get("line_items") or [])
        if li.get("catalog_object_id")
    })
    if not var_ids:
        return {}

    var_to_item: dict[str, str] = {}
    item_category: dict[str, str] = {}

    # Step 1: fetch ITEM_VARIATIONs + related ITEM objects
    for i in range(0, len(var_ids), 100):
        chunk = var_ids[i:i + 100]
        resp = client.post("/v2/catalog/batch-retrieve", body={
            "object_ids": chunk, "include_related_objects": True,
        })
        for obj in (resp.get("objects") or []):
            if obj.get("type") == "ITEM_VARIATION":
                var_to_item[obj["id"]] = obj.get("item_variation_data", {}).get("item_id", "")
        for obj in (resp.get("related_objects") or []):
            if obj.get("type") == "ITEM":
                # reporting_category is Square's primary category; fall back to
                # first entry in categories[] list for legacy items.
                rc = obj.get("item_data", {}).get("reporting_category") or {}
                cat_id = rc.get("id") or obj.get("item_data", {}).get("category_id")
                if not cat_id:
                    cats = obj.get("item_data", {}).get("categories") or []
                    cat_id = cats[0].get("id") if cats else None
                if cat_id:
                    item_category[obj["id"]] = cat_id

    # Step 2: fetch CATEGORY objects by ID to get their names
    cat_ids = sorted(set(item_category.values()))
    cat_name_by_id: dict[str, str] = {}
    for i in range(0, len(cat_ids), 100):
        chunk = cat_ids[i:i + 100]
        resp = client.post("/v2/catalog/batch-retrieve", body={"object_ids": chunk})
        for obj in (resp.get("objects") or []):
            if obj.get("type") == "CATEGORY":
                cat_name_by_id[obj["id"]] = obj.get("category_data", {}).get("name", "")

    out: dict[str, str] = {}
    for var_id in var_ids:
        item_id = var_to_item.get(var_id, "")
        cat_id = item_category.get(item_id, "")
        out[var_id] = cat_name_by_id.get(cat_id, "")
    return out


# ── main export ───────────────────────────────────────────────────


def export_window(*, start_date: datetime.date, end_date: datetime.date,
                   store: str = "palmetto", client: SquareClient | None = None,
                   profile: dict | None = None) -> dict:
    """Fetch Payments + Orders for [start_date, end_date] and write synthesized
    transactions/items CSVs. Returns {'transactions_csv': Path, 'items_csv': Path}.
    """
    from agents.bhaga.scripts.backfill_bigquery import load_store_profile
    profile = profile or load_store_profile(store)
    display_tz = profile.get("timezone", {}).get("square_account_display_tz", DEFAULT_DISPLAY_TZ)
    client = client or SquareClient(store)

    location_id, location_name = _resolve_location(client, profile)
    begin_time, end_time = _rfc3339_day_bounds(start_date, end_date, display_tz)

    payments = client.get_paginated("/v2/payments", params={
        "begin_time": begin_time, "end_time": end_time,
        "location_id": location_id, "limit": 100, "sort_order": "ASC",
    }, items_key="payments")

    # Refunds in the same window: the dashboard export includes them as
    # event_type=Refund rows with negative amounts (they reduce the tip pool).
    refunds = client.get_paginated("/v2/refunds", params={
        "begin_time": begin_time, "end_time": end_time,
        "location_id": location_id, "limit": 100, "sort_order": "ASC",
    }, items_key="refunds")

    order_ids = sorted(
        {p.get("order_id") for p in payments if p.get("order_id")}
        | {r.get("order_id") for r in refunds if r.get("order_id")}
    )
    orders_by_id: dict[str, dict] = {}
    for i in range(0, len(order_ids), 100):
        chunk = order_ids[i:i + 100]
        resp = client.post("/v2/orders/batch-retrieve", body={
            "location_id": location_id, "order_ids": chunk,
        })
        for o in resp.get("orders", []) or []:
            orders_by_id[o.get("id")] = o

    team = _team_member_map(client)
    categories = _category_map(client, list(orders_by_id.values()))

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    end_plus = end_date + datetime.timedelta(days=1)
    txn_path = DOWNLOADS_DIR / f"transactions-{start_date.isoformat()}-{end_plus.isoformat()}.csv"
    items_path = DOWNLOADS_DIR / f"items-{start_date.isoformat()}-{end_plus.isoformat()}.csv"

    tz_label = _tz_label(display_tz)
    txn_rows = _build_transaction_rows(payments, orders_by_id, team, location_name,
                                       display_tz, tz_label)
    txn_rows += _build_refund_rows(refunds, orders_by_id, team, location_name,
                                   display_tz, tz_label)
    item_rows = _build_item_rows(payments, orders_by_id, team, categories, location_name,
                                 display_tz, tz_label)

    _write_csv(txn_path, TXN_HEADER, txn_rows)
    _write_csv(items_path, ITEM_HEADER, item_rows)
    print(f"[square_api.export] wrote {len(txn_rows)} txn rows -> {txn_path}")
    print(f"[square_api.export] wrote {len(item_rows)} item rows -> {items_path}")
    return {"transactions_csv": txn_path, "items_csv": items_path}


# Map Square API order.source.name → Square dashboard CSV "Source" column.
# Only two API names differ from the CSV: empty → "Register", "Kiosk" → "Square Kiosk".
# All other API source.name values (e.g. "DoorDash - Storefront") appear verbatim in the CSV.
_SOURCE_LABEL: dict[str, str] = {
    "": "Register",
    "Point of Sale": "Register",  # Square POS app; treated identically to empty source
    "Kiosk": "Square Kiosk",
}

# Item CSV "channel" column: the platform label shown per-item by the dashboard.
# Dashboard strips "- Storefront" suffix from delivery platform names.
# POS Register (empty source) falls back to location_name (handled in caller).
# Kiosk and Per Diem use the source name directly as the channel.
_CHANNEL_LABEL: dict[str, str] = {
    "Uber Eats": "Uber Eats",
    "Uber Eats - Storefront": "Uber Eats",
    "DoorDash": "DoorDash",
    "DoorDash - Storefront": "DoorDash",
    "Square Online": "Square Online",
    "Kiosk": "Kiosk",
    "Square Kiosk": "Kiosk",
    "Per Diem": "Per Diem",
}


def _build_transaction_rows(payments, orders_by_id, team, location_name,
                            display_tz, tz_label) -> list[list[str]]:
    """Build one transaction row per ORDER (matching the dashboard CSV export).

    The dashboard CSV groups split-tender payments into a single row per order
    (transaction). For orders with multiple payments (split tender), we use the
    FIRST payment's timestamp and staff member for row metadata, and sum
    tip_money and processing_fee across all payments. Order-level gross/tax/
    total come from the order object and are already the aggregated totals.
    """
    # Group payments by order_id so split-tender orders emit one row.
    from collections import defaultdict
    by_order: dict[str, list] = defaultdict(list)
    no_order: list = []
    for p in payments:
        oid = p.get("order_id")
        if oid:
            by_order[oid].append(p)
        else:
            no_order.append(p)  # Direct payment (no order), keep as-is

    rows: list[list[str]] = []

    # One row per order (combining split tenders)
    for oid, pays in by_order.items():
        pays_sorted = sorted(pays, key=lambda x: x.get("created_at", ""))
        first_pay = pays_sorted[0]
        order = orders_by_id.get(oid, {})
        # Dashboard CSV timestamp convention (verified 2026-06-23 against prod BQ):
        # - Register (empty source): closed_at (order finalized = payment collected)
        # - Kiosk, 3rd-party (Uber Eats, DoorDash): created_at (order received = customer paid)
        raw_source_for_ts = (order.get("source") or {}).get("name", "")
        if raw_source_for_ts == "":
            txn_ts = order.get("closed_at") or first_pay.get("created_at", "")
        else:
            txn_ts = order.get("created_at") or first_pay.get("created_at", "")
        date_s, time_s = _created_at_to_display(txn_ts, display_tz)
        # Gift card purchases (item_type='GIFT_CARD') are excluded from gross/net
        # sales on the dashboard CSV — they are stored-value purchases, not sales.
        saleable_items = [
            li for li in (order.get("line_items") or [])
            if li.get("item_type") != "GIFT_CARD"
        ]
        gross = sum(_money_to_cents(li.get("gross_sales_money")) for li in saleable_items)
        discount = sum(_money_to_cents(li.get("total_discount_money")) for li in saleable_items)
        tip = sum(_money_to_cents(p.get("tip_money")) for p in pays_sorted)
        total_collected = _money_to_cents(
            order.get("net_amounts", {}).get("total_money")
        ) or sum(_money_to_cents(p.get("total_money")) for p in pays_sorted)
        fees = sum(
            _money_to_cents(f.get("amount_money"))
            for p in pays_sorted for f in (p.get("processing_fee") or [])
        )
        net_total = total_collected - fees
        tax = _money_to_cents(order.get("total_tax_money"))
        staff = team.get(first_pay.get("team_member_id", ""), "")
        raw_source = (order.get("source") or {}).get("name", "")
        # Map API source.name to dashboard CSV "Source" column label.
        # Only empty→Register and Kiosk→Square Kiosk differ; all others pass through.
        source = _SOURCE_LABEL.get(raw_source, raw_source)

        row = [""] * 55
        row[0] = date_s
        row[1] = time_s
        row[2] = tz_label
        row[3] = _money(gross)
        row[4] = _money(-abs(discount))
        row[6] = _money(gross - abs(discount))
        row[8] = _money(tax)
        row[9] = _money(tip)
        row[11] = _money(total_collected)
        row[12] = source
        row[20] = _money(-abs(fees))
        row[21] = _money(net_total)
        row[22] = oid  # Transaction ID = Order ID (dashboard convention)
        row[23] = first_pay.get("id", "")  # Payment ID = first payment
        row[27] = staff
        row[31] = "Payment"
        row[32] = location_name
        row[46] = "Complete"
        rows.append(row)

    # Direct payments without an order (no-order path, very rare)
    for p in no_order:
        order = {}
        # Use updated_at (closest to transaction completion) for no-order payments.
        txn_ts = p.get("updated_at") or p.get("created_at", "")
        date_s, time_s = _created_at_to_display(txn_ts, display_tz)
        tip = _money_to_cents(p.get("tip_money"))
        total_collected = _money_to_cents(p.get("total_money"))
        fees = sum(_money_to_cents(f.get("amount_money"))
                   for f in (p.get("processing_fee") or []))
        net_total = total_collected - fees
        staff = team.get(p.get("team_member_id", ""), "")
        pid = p.get("id", "")
        row = [""] * 55
        row[0] = date_s
        row[1] = time_s
        row[2] = tz_label
        row[9] = _money(tip)
        row[11] = _money(total_collected)
        row[20] = _money(-abs(fees))
        row[21] = _money(net_total)
        row[22] = pid  # Use payment_id as txn_id for no-order payments
        row[23] = pid
        row[27] = staff
        row[31] = "Payment"
        row[32] = location_name
        row[46] = "Complete"
        rows.append(row)

    return rows


def _build_refund_rows(refunds, orders_by_id, team, location_name,
                       display_tz, tz_label,
                       client: "SquareClient | None" = None) -> list[list[str]]:
    """One Refund row per PaymentRefund, with negative amounts (mirrors the
    dashboard export, where refunds appear as event_type=Refund rows that
    subtract from gross/tip/collected).

    When ``client`` is provided, the original payment is fetched to split the
    refund amount into gross and tip components (matching the dashboard CSV).
    """
    rows: list[list[str]] = []
    for r in refunds:
        date_s, time_s = _created_at_to_display(r.get("created_at", ""), display_tz)
        total_amount = _money_to_cents(r.get("amount_money"))
        fees = sum(_money_to_cents(f.get("amount_money"))
                   for f in (r.get("processing_fee") or []))
        # Dashboard shows empty staff for refunds (confirmed against prod BQ).
        staff = ""
        # Refund source: the dashboard CSV shows "Register" for refunds (the
        # refund is processed at the register, regardless of original order source).
        # The refund order (r.order_id) typically has no source.name → "Register".
        refund_order = orders_by_id.get(r.get("order_id", ""), {})
        raw_refund_src = (refund_order.get("source") or {}).get("name", "")
        orig_source = _SOURCE_LABEL.get(raw_refund_src, raw_refund_src or "Register")

        # Determine gross_amount and tip_amount components.
        # PaymentRefund.tip_money (if set) holds the tip portion explicitly.
        # Otherwise, look up the original payment to split correctly.
        if r.get("tip_money"):
            tip_amount = _money_to_cents(r["tip_money"])
            gross_amount = total_amount - tip_amount
        elif client is not None and r.get("payment_id"):
            try:
                pay_resp = client.get(f'/v2/payments/{r["payment_id"]}')
                orig = pay_resp.get("payment", {})
                orig_total = _money_to_cents(orig.get("total_money"))
                orig_tip = _money_to_cents(orig.get("tip_money"))
                if orig_total > 0:
                    ratio = total_amount / orig_total
                    tip_amount = round(orig_tip * ratio)
                    gross_amount = total_amount - tip_amount
                else:
                    tip_amount = 0
                    gross_amount = total_amount
            except Exception:  # noqa: BLE001
                tip_amount = 0
                gross_amount = total_amount
        else:
            tip_amount = 0
            gross_amount = total_amount

        row = [""] * 55
        row[0] = date_s
        row[1] = time_s
        row[2] = tz_label
        row[3] = _money(-abs(gross_amount))
        row[4] = _money(0)
        row[6] = _money(-abs(gross_amount))
        row[9] = _money(-abs(tip_amount))
        row[11] = _money(-abs(total_amount))
        row[20] = _money(abs(fees))
        row[21] = _money(-abs(total_amount) + abs(fees))
        row[12] = orig_source
        row[22] = r.get("order_id") or r.get("payment_id", "")
        row[23] = r.get("payment_id", "")
        row[27] = staff
        row[31] = "Refund"
        row[32] = location_name
        row[46] = "Complete"
        rows.append(row)
    return rows


def _build_item_rows(payments, orders_by_id, team, categories, location_name,
                     display_tz, tz_label) -> list[list[str]]:
    # First payment per order: the dashboard Item Detail rows carry the
    # PAYMENT timestamp (not order created_at) — and item_sold_at_local is
    # part of the BQ natural key, so this must match the scrape exactly.
    pay_by_order: dict[str, dict] = {}
    for p in payments:
        oid = p.get("order_id")
        if oid and oid not in pay_by_order:
            pay_by_order[oid] = p

    rows: list[list[str]] = []
    for oid, order in orders_by_id.items():
        payment = pay_by_order.get(oid)
        if payment is None:
            continue  # refund-only order; item refund rows are out of scope here
        # Item rows use the same timestamp convention as transaction rows.
        raw_src_for_ts = (order.get("source") or {}).get("name", "")
        if raw_src_for_ts == "":
            created_at = order.get("closed_at") or payment.get("created_at", "")
        else:
            created_at = order.get("created_at") or payment.get("created_at", "")
        date_s, time_s = _created_at_to_display(created_at, display_tz)
        staff = team.get(payment.get("team_member_id", ""), "")
        raw_src = (order.get("source") or {}).get("name", "")
        # Item CSV "channel": 3rd-party/Kiosk/Per Diem uses the mapped platform
        # label; Register (empty source or "Point of Sale") uses location_name.
        if _SOURCE_LABEL.get(raw_src, raw_src) == "Register":
            channel = location_name
        else:
            channel = _CHANNEL_LABEL.get(raw_src, raw_src)
        for li in (order.get("line_items") or []):
            # Gift card purchases appear in item_lines from the API but the
            # dashboard CSV item export excludes them (gift cards are not sold
            # inventory, they are stored-value instruments).
            if li.get("item_type") == "GIFT_CARD":
                continue
            gross = _money_to_cents(li.get("gross_sales_money"))
            discount = _money_to_cents(li.get("total_discount_money"))
            net = gross - abs(discount)
            qty = li.get("quantity", "1")
            cat = categories.get(li.get("catalog_object_id", ""), "")

            row = [""] * 31
            row[0] = date_s
            row[1] = time_s
            row[2] = tz_label
            row[3] = cat
            row[4] = li.get("name", "")
            row[5] = str(qty)
            row[9] = _money(gross)
            row[10] = _money(-abs(discount))
            row[11] = _money(net)
            row[13] = oid
            row[14] = payment.get("id", "")
            row[18] = "Payment"
            row[19] = location_name
            row[28] = staff
            row[30] = channel         # _ITEM_COL["channel"] = 30
            rows.append(row)
    return rows


def _build_refund_item_rows(refunds, orders_by_id, team, categories, location_name,
                            display_tz, tz_label,
                            client: "SquareClient | None" = None) -> list[list[str]]:
    """Build Refund event_type item rows matching the dashboard CSV Item Detail.

    For each refund, the dashboard emits one item row per line item from the
    ORIGINAL payment's order, with negated amounts and transaction_id = the
    refund's order_id. The timestamp is the refund's created_at.
    """
    rows: list[list[str]] = []
    for r in refunds:
        refund_oid = r.get("order_id") or r.get("payment_id", "")
        date_s, time_s = _created_at_to_display(r.get("created_at", ""), display_tz)
        # Find the original payment's order to get line items
        orig_pay = None
        if client is not None and r.get("payment_id"):
            try:
                pay_resp = client.get(f'/v2/payments/{r["payment_id"]}')
                orig_pay = pay_resp.get("payment", {})
            except Exception:  # noqa: BLE001
                pass
        if orig_pay is None:
            continue
        orig_oid = orig_pay.get("order_id", "")
        orig_order = orders_by_id.get(orig_oid)
        if orig_order is None and client is not None and orig_oid:
            try:
                orig_order = client.get(f'/v2/orders/{orig_oid}').get("order", {})
            except Exception:  # noqa: BLE001
                orig_order = {}
        if not orig_order:
            continue
        raw_src = (orig_order.get("source") or {}).get("name", "")
        if _SOURCE_LABEL.get(raw_src, raw_src) == "Register":
            channel = location_name
        else:
            channel = _CHANNEL_LABEL.get(raw_src, raw_src)
        staff = team.get(orig_pay.get("team_member_id", ""), "")
        for li in (orig_order.get("line_items") or []):
            if li.get("item_type") == "GIFT_CARD":
                continue
            gross = _money_to_cents(li.get("gross_sales_money"))
            discount = _money_to_cents(li.get("total_discount_money"))
            net = gross - abs(discount)
            # qty_sold is negative for refund items (matching dashboard CSV behavior)
            qty = "-" + li.get("quantity", "1")
            cat = categories.get(li.get("catalog_object_id", ""), "")
            row = [""] * 31
            row[0] = date_s
            row[1] = time_s
            row[2] = tz_label
            row[3] = cat
            row[4] = li.get("name", "")
            row[5] = qty
            row[9] = _money(-abs(gross))
            row[10] = _money(abs(discount))
            row[11] = _money(-abs(net))
            row[13] = refund_oid
            row[14] = r.get("payment_id", "")
            row[18] = "Refund"
            row[19] = location_name
            row[28] = staff
            row[30] = channel
            rows.append(row)
    return rows


def _write_csv(path: pathlib.Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for r in rows:
            writer.writerow(r)


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Square API -> transactions/items CSV export")
    cli.add_argument("--store", default="palmetto")
    cli.add_argument("--start", required=True, help="YYYY-MM-DD")
    cli.add_argument("--end", required=True, help="YYYY-MM-DD")
    cli.add_argument("--load-bq", action="store_true",
                     help="After writing CSVs, run backfill_from_downloads to load BQ "
                          "(honors BHAGA_BQ_DATASET for sandbox isolation).")
    args = cli.parse_args()

    res = export_window(
        start_date=datetime.date.fromisoformat(args.start),
        end_date=datetime.date.fromisoformat(args.end),
        store=args.store,
    )
    print(res)

    if args.load_bq:
        import subprocess
        # KDS via the Reporting API path (kept separate so a failure here does
        # not block the transactions/items load).
        try:
            from skills.square_api import kds_reporting
            kds_reporting.export_window_kds(
                start_date=datetime.date.fromisoformat(args.start),
                end_date=datetime.date.fromisoformat(args.end),
                store=args.store,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[square_api.export] KDS reporting skipped: {exc}", file=sys.stderr)
        cmd = [sys.executable, "-m", "agents.bhaga.scripts.backfill_from_downloads",
               "--store", args.store, "--start", args.start, "--end", args.end]
        print(f"[square_api.export] running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
