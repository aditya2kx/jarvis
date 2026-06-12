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
# (_ITEM_COL) are real; the rest are placeholders the parser ignores. The
# parser only validates that "Transaction ID" is present and reads by index.
ITEM_HEADER = [
    "Date", "Time", "Time Zone", "Category", "Item", "Qty",
    "Price Point Name", "SKU", "Modifiers Applied", "Gross Sales",
    "Discounts", "Net Sales", "Tax", "Transaction ID", "Payment ID",
    "Device Name", "Col16", "Col17", "Event Type", "Location",
    "Dining Option", "Col21", "Col22", "Col23", "Unit", "Count",
    "Col26", "Employee", "Col28", "Channel", "Col30",
]

DEFAULT_DISPLAY_TZ = "America/New_York"


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
    """catalog_object_id (item variation) -> category name, via Catalog
    batch-retrieve with related objects."""
    var_ids = sorted({
        li.get("catalog_object_id")
        for o in orders for li in (o.get("line_items") or [])
        if li.get("catalog_object_id")
    })
    if not var_ids:
        return {}
    cat_name_by_id: dict[str, str] = {}
    item_category: dict[str, str] = {}
    var_to_item: dict[str, str] = {}
    # batch-retrieve in chunks of 100
    objs: list[dict] = []
    related: list[dict] = []
    for i in range(0, len(var_ids), 100):
        chunk = var_ids[i:i + 100]
        resp = client.post("/v2/catalog/batch-retrieve", body={
            "object_ids": chunk, "include_related_objects": True,
        })
        objs.extend(resp.get("objects", []) or [])
        related.extend(resp.get("related_objects", []) or [])
    for obj in related:
        if obj.get("type") == "CATEGORY":
            cat_name_by_id[obj.get("id")] = obj.get("category_data", {}).get("name", "")
        if obj.get("type") == "ITEM":
            cat_id = obj.get("item_data", {}).get("category_id") or obj.get(
                "item_data", {}).get("reporting_category", {}).get("id")
            if cat_id:
                item_category[obj.get("id")] = cat_id
    for obj in objs:
        if obj.get("type") == "ITEM_VARIATION":
            var_to_item[obj.get("id")] = obj.get("item_variation_data", {}).get("item_id", "")
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

    order_ids = sorted({p.get("order_id") for p in payments if p.get("order_id")})
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
    item_rows = _build_item_rows(payments, orders_by_id, team, categories, location_name,
                                 display_tz, tz_label)

    _write_csv(txn_path, TXN_HEADER, txn_rows)
    _write_csv(items_path, ITEM_HEADER, item_rows)
    print(f"[square_api.export] wrote {len(txn_rows)} txn rows -> {txn_path}")
    print(f"[square_api.export] wrote {len(item_rows)} item rows -> {items_path}")
    return {"transactions_csv": txn_path, "items_csv": items_path}


def _build_transaction_rows(payments, orders_by_id, team, location_name,
                            display_tz, tz_label) -> list[list[str]]:
    rows: list[list[str]] = []
    for p in payments:
        order = orders_by_id.get(p.get("order_id"), {})
        date_s, time_s = _created_at_to_display(p.get("created_at", ""), display_tz)
        gross = sum(_money_to_cents(li.get("gross_sales_money"))
                    for li in (order.get("line_items") or []))
        discount = _money_to_cents(order.get("total_discount_money"))
        tip = _money_to_cents(p.get("tip_money"))
        total_collected = _money_to_cents(
            order.get("net_amounts", {}).get("total_money")) or _money_to_cents(p.get("total_money"))
        fees = sum(_money_to_cents(f.get("amount_money"))
                   for f in (p.get("processing_fee") or []))
        net_total = total_collected - fees
        tax = _money_to_cents(order.get("total_tax_money"))
        staff = team.get(p.get("team_member_id", ""), "")
        source = (order.get("source") or {}).get("name", "")

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
        row[22] = p.get("order_id", "")
        row[23] = p.get("id", "")
        row[27] = staff
        row[31] = "Payment"
        row[32] = location_name
        row[46] = "Complete"
        rows.append(row)
    return rows


def _build_item_rows(payments, orders_by_id, team, categories, location_name,
                     display_tz, tz_label) -> list[list[str]]:
    # payment id per order (first payment) for the Payment ID column.
    pay_by_order: dict[str, str] = {}
    for p in payments:
        oid = p.get("order_id")
        if oid and oid not in pay_by_order:
            pay_by_order[oid] = p.get("id", "")
    staff_by_order: dict[str, str] = {}
    for p in payments:
        oid = p.get("order_id")
        if oid and oid not in staff_by_order:
            staff_by_order[oid] = team.get(p.get("team_member_id", ""), "")

    rows: list[list[str]] = []
    for oid, order in orders_by_id.items():
        date_s, time_s = _created_at_to_display(order.get("created_at", ""), display_tz)
        source = (order.get("source") or {}).get("name", "")
        for li in (order.get("line_items") or []):
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
            row[14] = pay_by_order.get(oid, "")
            row[18] = "Payment"
            row[19] = location_name
            row[27] = staff_by_order.get(oid, "")
            row[29] = location_name
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
