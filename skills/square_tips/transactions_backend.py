#!/usr/bin/env python3
"""skills/square_tips/transactions_backend — Playwright-driven Transactions CSV export.

Differs from dashboard_backend.py (which scrapes the aggregated Sales Summary)
in that this drives the per-transaction Transactions report at
`app.squareup.com/dashboard/sales/transactions`. One row per transaction, 55
columns including transaction id, timestamp, gross sales, tip, net total,
source, staff name. Used to build the bhaga model sheet's daily / hour-of-day
breakdowns and as the source of truth for the daily tip pool.

Architecture mirrors dashboard_backend.py:
    * `build_plan()` produces a deterministic step list that the AI agent
      executes through the `user-playwright` MCP.
    * `parse_csv()` is pure-Python (no Playwright dep) so it can be unit
      tested and re-run against historical CSVs without a browser session.
    * `daily_transactions()` is the high-level entry point that picks the
      most recent matching CSV in `extracted/downloads/` and parses it.

Calibration & known quirks (see also selectors/transactions.json):
    * Account display timezone is Eastern Time, shop is in Austin (Central
      Time). `parse_csv()` converts to America/Chicago before deriving
      date_local / hour_local / dow_local. Never rely on the CSV's Date
      column for shop-day bucketing.
    * Export is asynchronous: click Generate, wait for the inline Download
      button to appear (1-60s depending on range size), then click Download.
    * Square names the file `transactions-YYYY-MM-DD-YYYY-MM-DD.csv` where
      the trailing date is end_date + 1 (exclusive end). Don't infer the
      actual range from the filename; use the data inside.

Status (2026-05-16): proven end-to-end with the Palmetto Superfoods account,
55-day backfill (Mar 22 - May 15, 2026). 2,956 transactions parsed; sum of
Total Collected matches the on-page summary exactly ($47,946.77).
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import pathlib
import re
import sys
from typing import Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from core.config_loader import project_dir
from skills.credentials import registry as cred_registry


_PROJECT = pathlib.Path(project_dir())
DOWNLOADS_DIR = _PROJECT / "extracted" / "downloads"
SELECTORS_PATH = _PROJECT / "skills" / "square_tips" / "selectors" / "transactions.json"

LOGIN_URL = "https://app.squareup.com/login"
LOGOUT_URL = "https://app.squareup.com/logout"
TRANSACTIONS_URL = "https://app.squareup.com/dashboard/sales/transactions"

# Shop is in Austin (America/Chicago). Override via build_plan(shop_tz=...)
# if/when we add the Houston location.
DEFAULT_SHOP_TZ = "America/Chicago"

# Square's "Time Zone" column uses human display names. Map to IANA so
# zoneinfo can DST-correctly convert. Extend as we encounter new ones.
_TZ_DISPLAY_TO_IANA = {
    "Eastern Time (US & Canada)": "America/New_York",
    "Central Time (US & Canada)": "America/Chicago",
    "Mountain Time (US & Canada)": "America/Denver",
    "Pacific Time (US & Canada)": "America/Los_Angeles",
    "Alaska Time (US & Canada)": "America/Anchorage",
    "Hawaii Time (US & Canada)": "Pacific/Honolulu",
    "Arizona Time (US & Canada)": "America/Phoenix",
}


# Column indexes in the Transactions CSV (verified 2026-05-16).
# Centralized here so the parser can refer to columns by semantic name
# rather than brittle numeric literals scattered through parse_csv().
_COL = {
    "date": 0,
    "time": 1,
    "time_zone": 2,
    "gross_sales": 3,
    "discounts": 4,
    "service_charges": 5,
    "net_sales": 6,
    "gift_card_sales": 7,
    "tax": 8,
    "tip": 9,
    "partial_refunds": 10,
    "total_collected": 11,
    "source": 12,
    "card": 13,
    "cash": 15,
    "net_total": 21,
    "transaction_id": 22,
    "payment_id": 23,
    "staff_name": 27,
    "staff_id": 28,
    "event_type": 31,
    "location": 32,
    "transaction_status": 46,
    "channel": 51,
    "unattributed_tips": 52,
}


# ── Credentials ───────────────────────────────────────────────────


def _credential_name(store: str) -> str:
    """Same credential as dashboard_backend uses — single Square login per store."""
    return f"square_{store.lower()}_login"


def get_credentials(store: str = "palmetto") -> dict:
    """Resolve Square dashboard login credentials. {'username', 'password'}."""
    entry = cred_registry.lookup(_credential_name(store))
    if not entry:
        raise RuntimeError(
            f"No credential '{_credential_name(store)}' in registry. Run the "
            f"collaborative login capture via skills/browser/collaborative.py "
            f"to populate it."
        )
    password = cred_registry.get_secret(_credential_name(store))
    return {"username": entry["account"], "password": password}


# ── Selectors ─────────────────────────────────────────────────────


def selectors() -> dict:
    return json.loads(SELECTORS_PATH.read_text())


# ── CSV parsing ───────────────────────────────────────────────────


def parse_money_cents(s: str) -> int:
    """Parse a money string into integer cents.

    Handles all forms Square has been observed to emit:
        '$13.50'   -> 1350
        '-$3.45'   -> -345     (Transactions CSV uses leading minus)
        '($36.75)' -> -3675    (Sales Summary Days CSV uses parens)
        '$0.00'    -> 0
        ''         -> 0
        '$1,234.56' -> 123456
    """
    s = (s or "").strip()
    if not s or s in ("$0.00", "0", "$0"):
        return 0
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    elif s.startswith("-"):
        negative = True
        s = s[1:]
    s = s.replace("$", "").replace(",", "").strip()
    if not s:
        return 0
    cents = int(round(float(s) * 100))
    return -cents if negative else cents


def _to_iana(tz_display: str) -> str:
    s = (tz_display or "").strip()
    iana = _TZ_DISPLAY_TO_IANA.get(s)
    if iana:
        return iana
    # Square exports the Time Zone column in the operator's browser locale.
    # When the operator is outside the US (e.g. traveling in India), Square
    # emits a raw IANA name like 'Asia/Calcutta' instead of one of the
    # human-readable US display strings above. Accept any value that
    # zoneinfo can resolve so the parser doesn't silently drop those rows.
    try:
        ZoneInfo(s)
        return s
    except Exception as exc:
        raise ValueError(
            f"Unknown Square Time Zone value {tz_display!r}. "
            f"Extend _TZ_DISPLAY_TO_IANA in transactions_backend.py "
            f"or fix the source export."
        ) from exc


def parse_transaction_rows(
    rows: list[list[str]],
    *,
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse an in-memory list of Transactions CSV rows (header + data).

    Identical logic to ``parse_csv`` but accepts rows already read into memory
    rather than a file path. Used by the Square API ingest path so no CSV file
    is written to disk.

    ``rows[0]`` must be the header row (e.g. TXN_HEADER from export.py).
    Returns the same record dicts as ``parse_csv``.
    """
    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    if "Transaction ID" not in header:
        return []

    target_tz = ZoneInfo(shop_tz)
    records: list[dict] = []
    for row in rows[1:]:
        if len(row) < len(_COL) or not row[_COL["transaction_id"]]:
            continue

        date_str = row[_COL["date"]].strip()
        time_str = row[_COL["time"]].strip()
        tz_display = row[_COL["time_zone"]]
        try:
            src_tz = ZoneInfo(_to_iana(tz_display))
        except ValueError:
            continue

        try:
            dt_src = datetime.datetime.fromisoformat(f"{date_str}T{time_str}").replace(
                tzinfo=src_tz
            )
        except ValueError:
            continue
        dt_local = dt_src.astimezone(target_tz)

        records.append({
            "transaction_id": row[_COL["transaction_id"]],
            "event_type": row[_COL["event_type"]],
            "created_at_src_iso": dt_src.isoformat(),
            "created_at_local_iso": dt_local.isoformat(),
            "date_local": dt_local.date().isoformat(),
            "hour_local": dt_local.hour,
            "dow_local": dt_local.weekday(),
            "gross_sales_cents": parse_money_cents(row[_COL["gross_sales"]]),
            "discount_cents": parse_money_cents(row[_COL["discounts"]]),
            "tip_cents": parse_money_cents(row[_COL["tip"]]),
            "net_total_cents": parse_money_cents(row[_COL["net_total"]]),
            "total_collected_cents": parse_money_cents(row[_COL["total_collected"]]),
            "source": row[_COL["source"]],
            "staff_name": row[_COL["staff_name"]],
            "location": row[_COL["location"]],
            "raw_date_csv": date_str,
            "raw_time_csv": time_str,
            "raw_tz_csv": tz_display,
        })

    records.sort(key=lambda r: r["created_at_local_iso"])
    return records


def parse_csv(
    csv_path: pathlib.Path,
    *,
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse a downloaded Transactions CSV into canonical per-transaction records.

    Each output dict represents one transaction (one CSV row) with both the
    original ET-bucketed timestamps preserved (for audit) AND derived
    shop-local fields used by the model sheet:

        {
            "transaction_id": str,
            "event_type": "Payment" | "Refund",
            "created_at_src_iso": "2026-05-15T21:38:24-04:00",  # Square's source TZ
            "created_at_local_iso": "2026-05-15T20:38:24-05:00", # shop TZ (CT)
            "date_local": "2026-05-15",       # KEY for daily aggregation
            "hour_local": 20,                  # KEY for dow_hour heatmap
            "dow_local": 4,                    # 0=Monday
            "gross_sales_cents": int,
            "discount_cents": int,             # typically negative
            "tip_cents": int,                  # negative on refunds
            "net_total_cents": int,            # after Square fees
            "total_collected_cents": int,      # before Square fees
            "source": str,                     # Register | Square Kiosk | Uber Eats | ...
            "staff_name": str,                 # often empty (kiosk, third-party)
            "location": str,
            "raw_date_csv": str,               # original ET-bucketed date from CSV (audit)
            "raw_time_csv": str,
            "raw_tz_csv": str,
        }

    Records are returned sorted by created_at_local_iso ascending.
    Delegates to ``parse_transaction_rows`` after reading the file.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    # Some descriptions contain embedded newlines inside quoted cells. csv.reader
    # with newline='' handles this correctly. utf-8-sig strips a BOM if present.
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    return parse_transaction_rows(rows, shop_tz=shop_tz)


# ── Aggregations (used by tip_pool_allocation + the model sheet) ───


def aggregate_daily_tip_pool(records: list[dict]) -> dict[str, int]:
    """Sum tip_cents by date_local. Refund tips count as negative (reduces pool).

    Returns {'YYYY-MM-DD': cents}. Days with $0 net tips are still included
    (value 0) so downstream code doesn't have to special-case missing days.
    """
    by_day: dict[str, int] = {}
    for r in records:
        d = r["date_local"]
        by_day[d] = by_day.get(d, 0) + r["tip_cents"]
    return by_day


def aggregate_daily_sales(records: list[dict]) -> dict[str, dict]:
    """Sum sales metrics by date_local.

    All amounts are in cents. Naming mirrors Square's CSV columns exactly
    so the operator can cross-reference Square reports:
      gross_sales_cents     — Square "Gross Sales" (pre-discount item revenue)
      discount_cents        — Square "Discounts" (typically negative)
      net_sales_cents       — Square "Net Sales" (post-discount item revenue,
                              the standard restaurant labor% denominator)
      tip_cents             — Square "Tip" (separate from sales)
      total_collected_cents — Square "Total Collected" (net_sales + tax +
                              service charges + tip)
      transaction_count     — count of ALL CSV rows (Payments + Refunds).
      refund_count          — count of rows with event_type == "Refund".
      order_count           — count of rows with event_type != "Refund"
                              (completed Payment events; labor-saturation
                              throughput denominator).
    """
    by_day: dict[str, dict] = {}
    for r in records:
        d = r["date_local"]
        bucket = by_day.setdefault(d, {
            "gross_sales_cents": 0,
            "discount_cents": 0,
            "net_sales_cents": 0,
            "total_collected_cents": 0,
            "tip_cents": 0,
            "transaction_count": 0,
            "refund_count": 0,
            "order_count": 0,
        })
        gross_c = r["gross_sales_cents"]
        disc_c = r["discount_cents"]
        bucket["gross_sales_cents"] += gross_c
        bucket["discount_cents"] += disc_c
        # Square's Net Sales = Gross Sales + Discounts (discounts are stored
        # as negative numbers, so the addition yields the post-discount
        # figure). Derive here so historical raw-sheet rows (which don't
        # carry net_sales_cents) get the right value too.
        bucket["net_sales_cents"] += gross_c + disc_c
        bucket["total_collected_cents"] += r["total_collected_cents"]
        bucket["tip_cents"] += r["tip_cents"]
        bucket["transaction_count"] += 1
        if r["event_type"] == "Refund":
            bucket["refund_count"] += 1
        else:
            # Completed orders only (Payment events) — matches the throughput
            # denominator used by labor saturation columns in the model sheet.
            bucket["order_count"] += 1
    return by_day


# ── Item Sales CSV parsing ────────────────────────────────────────

# Column indexes in the Item Sales Detail CSV (verified 2026-05-28 against
# extracted/downloads/items-2026-04-01-2026-05-01.csv).
_ITEM_COL = {
    "date": 0,
    "time": 1,
    "time_zone": 2,
    "category": 3,
    "item": 4,
    "qty": 5,
    "price_point_name": 6,
    "sku": 7,
    "modifiers_applied": 8,
    "gross_sales": 9,
    "discounts": 10,
    "net_sales": 11,
    "tax": 12,
    "transaction_id": 13,
    "payment_id": 14,
    "device_name": 15,
    "event_type": 18,
    "location": 19,
    "dining_option": 20,
    "unit": 24,
    "count": 25,
    "employee": 28,
    "channel": 30,
}


def parse_item_rows(
    rows: list[list[str]],
    *,
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse an in-memory list of Item Sales CSV rows (header + data).

    Identical logic to ``parse_item_sales_csv`` but accepts rows already read
    into memory. Used by the Square API ingest path (no CSV file on disk).
    ``rows[0]`` must be the header row (e.g. ITEM_HEADER from export.py).
    """
    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    if "Transaction ID" not in header:
        return []

    target_tz = ZoneInfo(shop_tz)
    records: list[dict] = []
    seq_by_group: dict[tuple[str, str, str], int] = {}
    for row in rows[1:]:
        if len(row) <= _ITEM_COL["transaction_id"] or not row[_ITEM_COL["transaction_id"]]:
            continue

        date_str = row[_ITEM_COL["date"]].strip()
        time_str = row[_ITEM_COL["time"]].strip()
        tz_display = row[_ITEM_COL["time_zone"]]
        try:
            src_tz = ZoneInfo(_to_iana(tz_display))
        except ValueError:
            continue

        try:
            dt_src = datetime.datetime.fromisoformat(f"{date_str}T{time_str}").replace(
                tzinfo=src_tz
            )
        except ValueError:
            continue
        dt_local = dt_src.astimezone(target_tz)

        qty_raw = (row[_ITEM_COL["qty"]] or "").strip()
        try:
            qty = float(qty_raw) if qty_raw else 0.0
        except ValueError:
            qty = 0.0

        date_local = dt_local.date().isoformat()
        time_local = dt_local.strftime("%H:%M:%S")
        item_sold_at_local = f"{date_local}T{time_local}"
        item_name = row[_ITEM_COL["item"]].strip()
        transaction_id = row[_ITEM_COL["transaction_id"]]
        group = (transaction_id, item_name, item_sold_at_local)
        line_seq = seq_by_group.get(group, 0)
        seq_by_group[group] = line_seq + 1
        records.append({
            "date_local": date_local,
            "time_local": time_local,
            "item_sold_at_local": item_sold_at_local,
            "item_name": item_name,
            "category": row[_ITEM_COL["category"]].strip(),
            "qty_sold": qty,
            "gross_sales_cents": parse_money_cents(row[_ITEM_COL["gross_sales"]]),
            "discount_cents": parse_money_cents(row[_ITEM_COL["discounts"]]),
            "net_sales_cents": parse_money_cents(row[_ITEM_COL["net_sales"]]),
            "transaction_id": transaction_id,
            "payment_id": row[_ITEM_COL["payment_id"]],
            "event_type": row[_ITEM_COL["event_type"]],
            "location": row[_ITEM_COL["location"]],
            "employee": row[_ITEM_COL["employee"]],
            "channel": row[_ITEM_COL["channel"]],
            "line_seq": line_seq,
        })

    records.sort(
        key=lambda r: (
            r["date_local"], r["time_local"],
            r["transaction_id"], r["item_name"], r["line_seq"],
        )
    )
    return records


def parse_item_sales_csv(
    csv_path: pathlib.Path,
    *,
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse a downloaded Item Sales Detail CSV into canonical per-item-line records.

    Each output dict represents one item line in a transaction (one CSV row):

        {
            "date_local": "2026-05-26",       # shop-TZ date
            "time_local": "19:41:18",          # shop-TZ time (HH:MM:SS)
            "item_sold_at_local": "2026-05-26T19:41:18",  # shop-TZ ISO datetime
            "item_name": "Blue Bondives Smoothie (16oz.)",
            "category": "Health Boost Smoothies",
            "qty_sold": 1.0,
            "gross_sales_cents": 1195,
            "discount_cents": 0,
            "net_sales_cents": 1195,
            "transaction_id": "4ECSV5...",
            "payment_id": "zZq3pU...",
            "event_type": "Payment",
            "location": "Austin Mueller Lake",
            "employee": "Lindsay Krause",
            "channel": "Austin Mueller Lake",
            "line_seq": 0,                     # 0-based within natural-key group
        }

    ``line_seq`` is a per-group counter over lines that share
    (transaction_id, item_name, item_sold_at_local), so the natural key stays
    stable across differently-windowed re-exports of the same data.

    Timezone conversion mirrors parse_csv(): the CSV's Date/Time/Time Zone
    columns are in the account's display timezone (varies by operator locale),
    and we convert to shop_tz (America/Chicago) for date_local bucketing.

    Records are returned sorted by
    (date_local, time_local, transaction_id, item_name, line_seq) ascending.
    Delegates to ``parse_item_rows`` after reading the file.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Item Sales CSV not found: {csv_path}")

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    return parse_item_rows(rows, shop_tz=shop_tz)


def aggregate_daily_item_stats(records: list[dict]) -> list[dict]:
    """Roll up item-level records to per-day stats.

    Input: output of parse_item_sales_csv().
    Output: one dict per shop-local day, sorted by date_local:

        {
            "date_local": "2026-05-26",
            "items_sold": 45,              # count of item line rows
            "units_sold": 48,              # sum of qty_sold (can exceed items_sold)
            "gross_sales_cents": 67500,
            "avg_item_price_cents": 1500,  # gross_sales_cents / items_sold (floor div)
        }

    Only Payment rows are counted (refund line items are excluded so the
    aggregate reflects actual throughput, matching aggregate_daily_sales
    order_count semantics).
    """
    by_day: dict[str, dict] = {}
    for r in records:
        if r.get("event_type") == "Refund":
            continue
        d = r["date_local"]
        bucket = by_day.setdefault(d, {
            "date_local": d,
            "items_sold": 0,
            "units_sold": 0.0,
            "gross_sales_cents": 0,
        })
        bucket["items_sold"] += 1
        bucket["units_sold"] += r["qty_sold"]
        bucket["gross_sales_cents"] += r["gross_sales_cents"]

    result = []
    for d in sorted(by_day):
        bucket = by_day[d]
        items = bucket["items_sold"]
        bucket["units_sold"] = int(round(bucket["units_sold"]))
        bucket["avg_item_price_cents"] = (
            bucket["gross_sales_cents"] // items if items > 0 else 0
        )
        result.append(bucket)
    return result


# ── KDS CSV parsing ───────────────────────────────────────────────


def parse_kds_dictrows(
    dict_rows: list[dict],
    *,
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse an in-memory list of KDS dict rows into canonical ticket records.

    Accepts a list of dicts with the same keys as the KDS CSV columns
    (Device Name, Ticket Name, Order Source, Number of Items, Items in Ticket,
    Completion Time (seconds), Time Created, Time Completed, Time Due,
    Time Recalled). Used by the Square Reporting API ingest path so no CSV
    file is written to disk.

    Returns the same record dicts as ``parse_kds_csv``.
    """
    target_tz = ZoneInfo(shop_tz)

    def _parse_ts(s: str):
        if not s:
            return None
        try:
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=target_tz)
            else:
                dt = dt.astimezone(target_tz)
            return dt
        except ValueError:
            for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.datetime.strptime(s, fmt).replace(tzinfo=target_tz)
                except ValueError:
                    continue
            return None

    records: list[dict] = []
    for row in dict_rows:
        device = (row.get("Device Name") or "").strip()
        ticket = (row.get("Ticket Name") or "").strip()
        order_source = (row.get("Order Source") or "").strip()

        num_items_raw = str(row.get("Number of Items") or "").strip()
        try:
            num_items = int(num_items_raw) if num_items_raw else 0
        except ValueError:
            num_items = 0

        comp_time_raw = str(row.get("Completion Time (seconds)") or "").strip()
        try:
            completion_time_sec = float(comp_time_raw) if comp_time_raw else 0.0
        except ValueError:
            completion_time_sec = 0.0

        created = _parse_ts(str(row.get("Time Created") or "").strip())
        completed = _parse_ts(str(row.get("Time Completed") or "").strip())
        due = _parse_ts(str(row.get("Time Due") or "").strip())
        recalled = _parse_ts(str(row.get("Time Recalled") or "").strip())

        if created is None:
            continue

        records.append({
            "device_name": device,
            "ticket_name": ticket,
            "order_source": order_source,
            "num_items": num_items,
            "items_in_ticket": str(row.get("Items in Ticket") or "").strip(),
            "completion_time_sec": completion_time_sec,
            "time_created": created,
            "time_completed": completed,
            "time_due": due,
            "time_recalled": recalled,
            "date_local": created.date().isoformat(),
        })

    records.sort(key=lambda r: r["time_created"])
    return records


def parse_kds_csv(
    csv_path: pathlib.Path,
    *,
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse the KDS kitchen-report.csv into a list of ticket dicts.

    CSV columns: Device Name, Ticket Name, Order Source, Number of Items,
    Items in Ticket, Completion Time (seconds), Time Created, Time Completed,
    Time Due, Time Recalled.

    Each output dict:
        {
            "device_name": str,
            "ticket_name": str,
            "order_source": str,
            "num_items": int,
            "items_in_ticket": str,
            "completion_time_sec": float,
            "time_created": datetime (shop-tz aware),
            "time_completed": datetime (shop-tz aware),
            "time_due": datetime | None,
            "time_recalled": datetime | None,
            "date_local": "YYYY-MM-DD",
        }
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"KDS CSV not found: {csv_path}")

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return []
        dict_rows = list(reader)

    return parse_kds_dictrows(dict_rows, shop_tz=shop_tz)


# Lower bound: tickets cleared in under 15s never had real prep (KDS bumped
# immediately / accidental clear) — kept as the ONLY filter. There is NO upper
# cap any more: KDS is now a purely OPERATIONAL-EFFICIENCY monitoring metric
# (it never feeds the staffing solver — that uses the flat config target), so
# we surface the FULL tail (p90/p95/p99) instead of hiding left-open artifacts.
# Percentiles + median are inherently robust to the left-open / left-open tail,
# so no cap is needed; the operator sees the real distribution and can coach.
KDS_MIN_COMPLETION_SEC = 15


def _percentile(sorted_vals: list, q: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' / type-7), q in [0,100].

    ``sorted_vals`` MUST be sorted ascending. Returns 0.0 for an empty list.
    Used for the KDS per-item p90/p95/p99/median. Pure + deterministic so
    weekly/period rollups pool the per-day item distributions and recompute
    TRUE percentiles (not an average-of-daily-percentiles).
    """
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    rank = (q / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def aggregate_daily_kds_stats(
    tickets: list[dict],
    *,
    min_completion_sec: float = KDS_MIN_COMPLETION_SEC,
) -> dict[str, dict]:
    """Aggregate per-ticket KDS data into daily OPERATIONAL-EFFICIENCY stats.

    Returns {date_iso: {
        completed_tickets: int,
        completed_items: int,
        median_time_per_item_sec: float,
        p90_time_per_item_sec: float,
        p95_time_per_item_sec: float,
        p99_time_per_item_sec: float,
        pct_tickets_late: float,
        shift_start: str (HH:MM),
        shift_end: str (HH:MM),
        late_tickets: int,
        due_tickets: int,
        per_item_times: list[int],   # item-weighted per-item seconds, sorted
    }}

    Per-item time = completion_time_sec / num_items for each ticket (guarded
    num_items > 0). The distribution is ITEM-WEIGHTED: a ticket of N items
    contributes N copies of its per-item time, so ``len(per_item_times) ==
    completed_items`` and ``kds_pct_items_over_goal`` (computed downstream) is a
    true share-of-ITEMS. ``per_item_times`` is stored per day so weekly/period
    rollups pool the raw item distributions and compute EXACT pooled
    percentiles + over-goal share (no average-of-averages approximation).

    The goal for pct_items_over_goal is NOT applied here — the model/config
    layer (update_model_sheet) injects the flat config target so changing the
    goal recomputes on the next rebuild without re-backfilling.

    Filtering: only the 15s lower floor (``min_completion_sec``) is applied —
    a ticket cleared in < 15s never had real prep (accidental clear). There is
    NO upper cap: the full tail is surfaced on purpose. Tickets with
    num_items <= 0 contribute to completed_tickets but add nothing to the
    per-item distribution.
    """
    by_day: dict[str, list[dict]] = {}
    # Every date that had ANY ticket (pre-filter). Dates whose tickets are ALL
    # below the 15s floor end up with no qualifying tickets; we still emit an
    # explicit ZERO row for them below so the idempotent raw-sheet upsert
    # OVERWRITES any stale row from an earlier (capped) run — the upsert keys on
    # date and never deletes, so a vanished day would otherwise keep old values.
    dates_with_any_ticket: set[str] = set()
    for t in tickets:
        dates_with_any_ticket.add(t["date_local"])
        if t["completion_time_sec"] < min_completion_sec:
            continue
        by_day.setdefault(t["date_local"], []).append(t)

    def _zero_row() -> dict:
        return {
            "completed_tickets": 0,
            "completed_items": 0,
            "median_time_per_item_sec": 0.0,
            "p90_time_per_item_sec": 0.0,
            "p95_time_per_item_sec": 0.0,
            "p99_time_per_item_sec": 0.0,
            "pct_tickets_late": 0.0,
            "shift_start": "",
            "shift_end": "",
            "late_tickets": 0,
            "due_tickets": 0,
            "per_item_times": [],
        }

    result: dict[str, dict] = {}
    for d in sorted(by_day):
        day_tickets = by_day[d]
        completed_tickets = len(day_tickets)
        completed_items = sum(t["num_items"] for t in day_tickets)

        # Item-weighted per-item completion times (one entry per ITEM). No cap —
        # the full tail (including left-open artifacts) is intentionally kept.
        per_item_times: list[int] = []
        for t in day_tickets:
            n = t["num_items"]
            if n > 0 and t["completion_time_sec"] > 0:
                tpi = t["completion_time_sec"] / n
                per_item_times.extend([int(round(tpi))] * n)
        per_item_times.sort()

        median_tpi = _percentile(per_item_times, 50)
        p90_tpi = _percentile(per_item_times, 90)
        p95_tpi = _percentile(per_item_times, 95)
        p99_tpi = _percentile(per_item_times, 99)

        # Late tickets: completed after time_due
        late_count = 0
        due_count = 0
        for t in day_tickets:
            if t["time_due"] is not None and t["time_completed"] is not None:
                due_count += 1
                if t["time_completed"] > t["time_due"]:
                    late_count += 1
        pct_late = (late_count / due_count) if due_count > 0 else 0.0

        # Shift envelope: first ticket created to last ticket completed
        created_times = [t["time_created"] for t in day_tickets if t["time_created"]]
        completed_times_dt = [t["time_completed"] for t in day_tickets if t["time_completed"]]
        shift_start = min(created_times).strftime("%H:%M") if created_times else ""
        shift_end = max(completed_times_dt).strftime("%H:%M") if completed_times_dt else ""

        result[d] = {
            "completed_tickets": completed_tickets,
            "completed_items": completed_items,
            "median_time_per_item_sec": round(median_tpi, 1),
            "p90_time_per_item_sec": round(p90_tpi, 1),
            "p95_time_per_item_sec": round(p95_tpi, 1),
            "p99_time_per_item_sec": round(p99_tpi, 1),
            "pct_tickets_late": round(pct_late, 4),
            "shift_start": shift_start,
            "shift_end": shift_end,
            # Exposed so weekly/period rollups recompute metrics EXACTLY by
            # POOLING the raw item distributions (percentiles + over-goal share
            # are not recoverable from daily summary stats — pct_tickets_late is
            # exact via Σlate/Σdue):
            "late_tickets": late_count,
            "due_tickets": due_count,
            "per_item_times": per_item_times,
        }

    # Emit ZERO rows for dates that had tickets but ALL were below the floor, so
    # a re-run overwrites any stale row from an earlier (capped) aggregation.
    for d in sorted(dates_with_any_ticket - set(result)):
        result[d] = _zero_row()
    return result


# ── Playwright playbook ───────────────────────────────────────────


def build_plan(
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    store: str = "palmetto",
    creds: Optional[dict] = None,
    shop_tz: str = DEFAULT_SHOP_TZ,
    max_generate_wait_seconds: int = 300,
) -> dict:
    """Produce a deterministic Playwright playbook for the AI agent.

    The plan covers the full from-scratch flow: forced logout, login (using
    Keychain-resolved password fetched at runtime, NOT embedded in the plan),
    navigate to Transactions, set the date range, trigger Generate, poll for
    Download readiness, click Download, and surface the saved CSV path.

    Square Transactions supports arbitrary date ranges in a single export.
    Unlike dashboard_backend.py, this skill does NOT need week-iteration.

    The download lands in DOWNLOADS_DIR as
    `transactions-{start}-{end_plus_1}.csv` (Square uses exclusive end in the
    filename). The AI sets `captures.csv_path` to the resolved path so the
    orchestrator can hand it to parse_csv().
    """
    sels = selectors()
    creds = creds or get_credentials(store)

    return {
        "store": store.lower(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "shop_tz": shop_tz,
        "creds_username": creds["username"],
        "captures": {
            "csv_path": None,
            "transaction_count_on_page": None,
            "total_collected_on_page": None,
            "errors": [],
        },
        "steps": [
            {
                "id": "logout",
                "action": "browser_navigate",
                "args": {"url": LOGOUT_URL},
                "description": "Force logout for a clean run state.",
                "postcondition": "URL contains '/login'",
            },
            {
                "id": "login_email",
                "action": "browser_type",
                "selectors_hint": "Use skills/square_tips/selectors/dashboard.json#login.email_input",
                "value": creds["username"],
                "description": "Step 1 of Square's two-step login flow.",
            },
            {
                "id": "login_continue",
                "action": "browser_click",
                "selectors_hint": "dashboard.json#login.continue_button",
                "description": "Advance to password step.",
            },
            {
                "id": "login_password",
                "action": "browser_type",
                "selectors_hint": "dashboard.json#login.password_input",
                "value_ref": "creds.password",
                "description": "Type password (Keychain-resolved at runtime, never embedded).",
            },
            {
                "id": "login_submit",
                "action": "browser_click",
                "selectors_hint": "dashboard.json#login.signin_button",
                "description": "Submit login.",
                "postcondition": "URL contains '/dashboard'",
            },
            {
                "id": "navigate_transactions",
                "action": "browser_navigate",
                "args": {"url": TRANSACTIONS_URL},
                "description": "Open the Transactions report.",
            },
            {
                "id": "wait_for_load",
                "action": "browser_wait_for",
                "args": {"time": 4},
                "description": "Let the SPA mount; date pill and Export trigger become reffable.",
            },
            {
                "id": "capture_page_summary_baseline",
                "action": "browser_snapshot",
                "description": (
                    "Snapshot the page-summary KPIs (Complete Transactions count + "
                    "Total Collected) BEFORE changing the date range so the AI can "
                    "later verify the CSV totals match the on-page numbers."
                ),
            },
            {
                "id": "open_date_picker",
                "action": "browser_click",
                "selectors_hint": (
                    "Click sels.transactions_page.date_range_pill.primary_text_pattern "
                    "(button matching /\\d{2}\\/\\d{2}\\/\\d{4}–\\d{2}\\/\\d{2}\\/\\d{4}/)."
                ),
                "description": "Open the date-range picker popover.",
            },
            {
                "id": "type_start_date",
                "action": "browser_type",
                "selectors_hint": (
                    "First text input inside the date-picker popover (the 'Start' field). "
                    "See sels.transactions_page.date_picker.start_date_input."
                ),
                "value": start_date.strftime("%m/%d/%Y"),
                "description": "Type start date in MM/DD/YYYY.",
            },
            {
                "id": "type_end_date",
                "action": "browser_type",
                "selectors_hint": (
                    "Second text input inside the date-picker popover (the 'End' field). "
                    "See sels.transactions_page.date_picker.end_date_input."
                ),
                "value": end_date.strftime("%m/%d/%Y"),
                "args": {"submit": True},
                "description": "Type end date and press Enter to apply the range.",
            },
            {
                "id": "wait_for_range_applied",
                "action": "browser_wait_for",
                "args": {"time": 2},
                "description": "Allow the report to refetch with the new range.",
            },
            {
                "id": "close_date_picker",
                "action": "browser_press_key",
                "args": {"key": "Escape"},
                "description": "Close the picker popover so the Export button is reachable.",
            },
            {
                "id": "verify_range_applied",
                "action": "browser_snapshot",
                "description": (
                    f"Confirm the page h4 header reads '{start_date.strftime('%b %-d, %Y')}"
                    f"–{end_date.strftime('%b %-d, %Y')}'. If not, alert via slack and abort."
                ),
            },
            {
                "id": "open_export_panel",
                "action": "browser_click",
                "selectors_hint": "sels.transactions_page.export_trigger_button",
                "description": "Expand the Export panel (does NOT start a download).",
            },
            {
                "id": "click_generate",
                "action": "browser_click",
                "selectors_hint": "sels.transactions_page.export_panel.generate_button.fallback_role",
                "description": "Start async CSV generation. Square shows a progress row.",
            },
            {
                "id": "poll_for_ready",
                "action": "browser_loop",
                "max_iterations": max(1, max_generate_wait_seconds // 5),
                "iteration_delay_seconds": 5,
                "break_when": (
                    "An element matching role='button' with name='Download Transactions CSV' "
                    "appears inside the Export panel (sels.transactions_page.export_panel."
                    "download_button)."
                ),
                "on_max_iterations": (
                    "Append to captures.errors: 'generate_timeout_after_"
                    f"{max_generate_wait_seconds}s'. DM via skills/slack and exit non-zero. "
                    "Do NOT silently retry: a stuck generation may indicate Square-side outage."
                ),
                "description": "Poll the Export panel until generation completes.",
            },
            {
                "id": "click_download",
                "action": "browser_click",
                "selectors_hint": "sels.transactions_page.export_panel.download_button.fallback_role",
                "description": (
                    "Trigger the actual file download. The browser_* MCP will report "
                    "'Downloaded file transactions-{start}-{end_plus_1}.csv to ...' in "
                    "the tool result; the AI must capture that path into captures.csv_path."
                ),
            },
            {
                "id": "wait_for_download",
                "action": "browser_wait_for",
                "args": {"time": 3},
                "description": "Brief settle so the file is fully flushed before parse_csv() reads it.",
            },
            {
                "id": "parse_and_validate",
                "action": "python",
                "description": (
                    "records = transactions_backend.parse_csv(captures.csv_path, "
                    f"shop_tz={shop_tz!r}); "
                    "assert sum(r['total_collected_cents'] for r in records) == "
                    "page_summary_total_collected_cents (within $0.01); else append to "
                    "captures.errors and alert via slack."
                ),
            },
        ],
    }


# ── Public entry point ────────────────────────────────────────────


def daily_transactions(
    start_date: datetime.date,
    end_date: datetime.date,
    *,
    store: str = "palmetto",
    shop_tz: str = DEFAULT_SHOP_TZ,
) -> list[dict]:
    """Parse the most recent Transactions CSV that covers the requested range.

    Square names the file with an exclusive end date (end_date + 1). We look
    for both the exact-match filename and, failing that, the most recent
    `transactions-*.csv` in DOWNLOADS_DIR as a debugging fallback.

    Returns the full per-transaction list. Caller (e.g.
    skills/tip_ledger_writer or the bhaga orchestrator) is responsible for
    further aggregation and Sheets I/O.
    """
    end_plus = end_date + datetime.timedelta(days=1)
    pattern = (
        f"transactions-{start_date.year}-{start_date.month:02d}-{start_date.day:02d}-"
        f"{end_plus.year}-{end_plus.month:02d}-{end_plus.day:02d}.csv"
    )
    candidates = list(DOWNLOADS_DIR.glob(pattern))
    if not candidates:
        candidates = sorted(
            DOWNLOADS_DIR.glob("transactions-*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"No Transactions CSV found matching {pattern} in {DOWNLOADS_DIR}. "
                f"Run build_plan() and have the AI drive Playwright to populate."
            )
    return parse_csv(candidates[0], shop_tz=shop_tz)


# ── CLI ───────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    cli = argparse.ArgumentParser(description="Square Transactions report extractor")
    sub = cli.add_subparsers(dest="cmd")

    p_parse = sub.add_parser("parse", help="Parse an existing Transactions CSV.")
    p_parse.add_argument("csv_path")
    p_parse.add_argument("--shop-tz", default=DEFAULT_SHOP_TZ)
    p_parse.add_argument(
        "--summary", action="store_true",
        help="Print per-day aggregates instead of the full record list.",
    )

    p_plan = sub.add_parser("plan", help="Print the Playwright playbook for a date range.")
    p_plan.add_argument("--start", required=True, help="YYYY-MM-DD")
    p_plan.add_argument("--end", required=True, help="YYYY-MM-DD")
    p_plan.add_argument("--store", default="palmetto")
    p_plan.add_argument("--shop-tz", default=DEFAULT_SHOP_TZ)

    p_creds = sub.add_parser("verify-creds", help="Check Keychain access.")
    p_creds.add_argument("--store", default="palmetto")

    args = cli.parse_args()

    if args.cmd == "parse":
        records = parse_csv(pathlib.Path(args.csv_path), shop_tz=args.shop_tz)
        if args.summary:
            sales = aggregate_daily_sales(records)
            tips = aggregate_daily_tip_pool(records)
            summary = {
                day: {**sales.get(day, {}), "tip_pool_cents": tips.get(day, 0)}
                for day in sorted(set(sales) | set(tips))
            }
            print(json.dumps(summary, indent=2))
        else:
            print(json.dumps(records, indent=2))
    elif args.cmd == "plan":
        plan = build_plan(
            datetime.date.fromisoformat(args.start),
            datetime.date.fromisoformat(args.end),
            store=args.store,
            shop_tz=args.shop_tz,
        )
        print(json.dumps(plan, indent=2))
    elif args.cmd == "verify-creds":
        creds = get_credentials(args.store)
        print(json.dumps({
            "store": args.store,
            "username": creds["username"],
            "password_length": len(creds["password"]),
            "password_preview": creds["password"][:2] + "***" + creds["password"][-2:],
        }, indent=2))
    else:
        cli.print_help()
