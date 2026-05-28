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
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    target_tz = ZoneInfo(shop_tz)

    # Some descriptions contain embedded newlines inside quoted cells. csv.reader
    # with newline='' handles this correctly. utf-8-sig strips a BOM if present.
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    if "Transaction ID" not in header:
        # Probably wrong CSV (e.g. Sales Summary). Caller fed the wrong path.
        return []

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
            # Skip rather than crash; log via Slack in production via the orchestrator.
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
        }

    Timezone conversion mirrors parse_csv(): the CSV's Date/Time/Time Zone
    columns are in the account's display timezone (varies by operator locale),
    and we convert to shop_tz (America/Chicago) for date_local bucketing.

    Records are returned sorted by (date_local, time_local) ascending.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Item Sales CSV not found: {csv_path}")

    target_tz = ZoneInfo(shop_tz)

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))

    if not rows or len(rows) < 2:
        return []

    header = rows[0]
    if "Transaction ID" not in header:
        return []

    records: list[dict] = []
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

        records.append({
            "date_local": dt_local.date().isoformat(),
            "time_local": dt_local.strftime("%H:%M:%S"),
            "item_name": row[_ITEM_COL["item"]].strip(),
            "category": row[_ITEM_COL["category"]].strip(),
            "qty_sold": qty,
            "gross_sales_cents": parse_money_cents(row[_ITEM_COL["gross_sales"]]),
            "discount_cents": parse_money_cents(row[_ITEM_COL["discounts"]]),
            "net_sales_cents": parse_money_cents(row[_ITEM_COL["net_sales"]]),
            "transaction_id": row[_ITEM_COL["transaction_id"]],
            "payment_id": row[_ITEM_COL["payment_id"]],
            "event_type": row[_ITEM_COL["event_type"]],
            "location": row[_ITEM_COL["location"]],
            "employee": row[_ITEM_COL["employee"]],
            "channel": row[_ITEM_COL["channel"]],
        })

    records.sort(key=lambda r: (r["date_local"], r["time_local"]))
    return records


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

    target_tz = ZoneInfo(shop_tz)

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return []

        records: list[dict] = []
        for row in reader:
            device = (row.get("Device Name") or "").strip()
            ticket = (row.get("Ticket Name") or "").strip()
            order_source = (row.get("Order Source") or "").strip()

            num_items_raw = (row.get("Number of Items") or "").strip()
            try:
                num_items = int(num_items_raw) if num_items_raw else 0
            except ValueError:
                num_items = 0

            comp_time_raw = (row.get("Completion Time (seconds)") or "").strip()
            try:
                completion_time_sec = float(comp_time_raw) if comp_time_raw else 0.0
            except ValueError:
                completion_time_sec = 0.0

            time_created_raw = (row.get("Time Created") or "").strip()
            time_completed_raw = (row.get("Time Completed") or "").strip()
            time_due_raw = (row.get("Time Due") or "").strip()
            time_recalled_raw = (row.get("Time Recalled") or "").strip()

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
                    # Try common formats
                    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                        try:
                            dt = datetime.datetime.strptime(s, fmt).replace(tzinfo=target_tz)
                            return dt
                        except ValueError:
                            continue
                    return None

            created = _parse_ts(time_created_raw)
            completed = _parse_ts(time_completed_raw)
            due = _parse_ts(time_due_raw)
            recalled = _parse_ts(time_recalled_raw)

            if created is None:
                continue

            date_local = created.date().isoformat()

            records.append({
                "device_name": device,
                "ticket_name": ticket,
                "order_source": order_source,
                "num_items": num_items,
                "items_in_ticket": (row.get("Items in Ticket") or "").strip(),
                "completion_time_sec": completion_time_sec,
                "time_created": created,
                "time_completed": completed,
                "time_due": due,
                "time_recalled": recalled,
                "date_local": date_local,
            })

    records.sort(key=lambda r: r["time_created"])
    return records


def aggregate_daily_kds_stats(tickets: list[dict]) -> dict[str, dict]:
    """Aggregate per-ticket KDS data into daily stats.

    Returns {date_iso: {
        completed_tickets: int,
        completed_items: int,
        avg_completion_time_sec: float,
        avg_time_per_item_sec: float,
        median_time_per_item_sec: float,
        pct_tickets_late: float,
        shift_start: str (HH:MM),
        shift_end: str (HH:MM),
    }}

    Filters outlier tickets with completion_time < 15 seconds
    (KDS cleared without actual prep).
    """
    import statistics

    by_day: dict[str, list[dict]] = {}
    for t in tickets:
        # Filter outliers: tickets cleared without prep
        if t["completion_time_sec"] < 15:
            continue
        d = t["date_local"]
        by_day.setdefault(d, []).append(t)

    result: dict[str, dict] = {}
    for d in sorted(by_day):
        day_tickets = by_day[d]
        completed_tickets = len(day_tickets)
        completed_items = sum(t["num_items"] for t in day_tickets)

        completion_times = [t["completion_time_sec"] for t in day_tickets if t["completion_time_sec"] > 0]
        avg_completion_time = (
            statistics.mean(completion_times) if completion_times else 0.0
        )

        # Time per item: completion_time / num_items for each ticket with items > 0
        time_per_item_values = [
            t["completion_time_sec"] / t["num_items"]
            for t in day_tickets
            if t["num_items"] > 0 and t["completion_time_sec"] > 0
        ]
        avg_time_per_item = (
            statistics.mean(time_per_item_values) if time_per_item_values else 0.0
        )
        median_time_per_item = (
            statistics.median(time_per_item_values) if time_per_item_values else 0.0
        )

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
            "avg_completion_time_sec": round(avg_completion_time, 1),
            "avg_time_per_item_sec": round(avg_time_per_item, 1),
            "median_time_per_item_sec": round(median_time_per_item, 1),
            "pct_tickets_late": round(pct_late, 4),
            "shift_start": shift_start,
            "shift_end": shift_end,
        }
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
