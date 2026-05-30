#!/usr/bin/env python3
"""Build and upsert BHAGA Model > item_operations from raw item_lines + punches."""

from __future__ import annotations

import datetime
from typing import Optional

from agents.bhaga.scripts.daily_refresh import is_refresh_date_complete
from skills.bhaga_labor.staff_punched_in import (
    count_staff_punched_in_at,
    index_punches_by_date,
)
from skills.tip_ledger_writer.writer import write_model_item_operations

_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _cents_to_dollars(cents: int | float) -> float:
    return round(float(cents) / 100.0, 2)


def _in_date_window(date_local: str, date_from: Optional[str], date_to: Optional[str]) -> bool:
    if date_from and date_local < date_from:
        return False
    if date_to and date_local > date_to:
        return False
    return True


def build_item_operations_records(
    *,
    item_lines: list[dict],
    punches: list[dict],
    wage_rates: list[dict],
    excluded_from_tip_pool: set[str],
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    now_ct: Optional[datetime.datetime] = None,
) -> list[dict]:
    """Derive model rows for item_operations (one dict per item line)."""
    punches_by_date = index_punches_by_date(punches)
    out: list[dict] = []

    for line in item_lines:
        date_local = line.get("date_local") or ""
        if not date_local:
            continue
        if not _in_date_window(date_local, date_from, date_to):
            continue
        try:
            d = datetime.date.fromisoformat(date_local)
        except ValueError:
            continue
        if not is_refresh_date_complete(d, now_ct=now_ct):
            continue

        sold_at = line.get("item_sold_at_local") or ""
        if not sold_at:
            continue

        counts = count_staff_punched_in_at(
            item_sold_at_local=sold_at,
            punches=punches,
            wage_rates=wage_rates,
            excluded_from_tip_pool=excluded_from_tip_pool,
            punches_by_date=punches_by_date,
        )

        out.append({
            "date_local": date_local,
            "item_sold_at_local": sold_at,
            "dow_label": _DOW[d.weekday()],
            "item_name": line.get("item_name", ""),
            "category": line.get("category", ""),
            "qty_sold": line.get("qty_sold", 0),
            "gross_sales_dollars": _cents_to_dollars(line.get("gross_sales_cents") or 0),
            "discount_dollars": _cents_to_dollars(line.get("discount_cents") or 0),
            "net_sales_dollars": _cents_to_dollars(line.get("net_sales_cents") or 0),
            "event_type": line.get("event_type", ""),
            "transaction_id": line.get("transaction_id", ""),
            "staff_punched_in_hourly_count": counts["staff_punched_in_hourly_count"],
            "staff_punched_in_fulltime_count": counts["staff_punched_in_fulltime_count"],
            "staff_punched_in_total_count": counts["staff_punched_in_total_count"],
            "line_seq": int(line.get("line_seq") or 0),
        })

    return out


def refresh_item_operations_tab(
    *,
    model_sid: str,
    square_raw_sid: str,
    adp_raw_sid: str,
    store: str,
    excluded_from_tip_pool: set[str],
    punches: list[dict],
    wage_rates: list[dict],
    item_lines: list[dict] | None = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    all_dates: bool = False,
    dry_run: bool = False,
    now_ct: Optional[datetime.datetime] = None,
) -> dict:
    """Read item_lines (if needed), build records, upsert item_operations tab."""
    if item_lines is None:
        from skills.tip_ledger_writer.reader import read_raw_square_item_lines
        item_lines = read_raw_square_item_lines(square_raw_sid, account=store)

    if all_dates:
        date_from = None
        date_to = None
    elif date_from is None and date_to is None and item_lines:
        dates = sorted({ln["date_local"] for ln in item_lines if ln.get("date_local")})
        if dates:
            date_from = dates[0]
            date_to = dates[-1]

    records = build_item_operations_records(
        item_lines=item_lines,
        punches=punches,
        wage_rates=wage_rates,
        excluded_from_tip_pool=excluded_from_tip_pool,
        date_from=date_from,
        date_to=date_to,
        now_ct=now_ct,
    )

    print(f"# item_operations: {len(records)} rows to upsert "
          f"(window={date_from!r}..{date_to!r}, all_dates={all_dates})")

    if dry_run:
        return {"dry_run": True, "incoming_records": len(records)}

    summary = write_model_item_operations(model_sid, records, account=store)
    print(
        f"    item_operations: +{summary['inserted']} new, "
        f"{summary['updated']} updated, {summary['total_after']} total"
    )
    return summary
