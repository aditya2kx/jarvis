#!/usr/bin/env python3
"""
Build the AKSHAYA HQ Inventory Forecast sheet — v2 with:
  - Max storage capacity (from 3/23 first message)
  - Order-to-95% recommendations
  - Event-aware days-of-supply (Media Day 5/4, Grand Opening 5/9 → +50% sustained)
"""

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from forecast_v2 import (
    MAX_CAPACITY, HQ_BASES, HQ_GRANOLAS, EVENT_WEEK_START, EVENT_BUMP,
    parse_inv, load_inventory_timeseries, compute_per_item_consumption,
    load_square_orders, _weekly, days_of_supply,
)

HQ_ALL = HQ_BASES + HQ_GRANOLAS


def build_cells():
    inventory = load_inventory_timeseries()
    dates = sorted(inventory.keys())
    latest_date = dates[-1]
    latest = inventory[latest_date]

    consumption = compute_per_item_consumption(inventory)
    daily_orders = load_square_orders()
    weekly = _weekly(daily_orders)

    base_daily_avg = sum(daily_orders.values()) / len(daily_orders)
    full_weeks = [w for w in weekly.values() if w['days'] == 7]
    current_daily_avg = full_weeks[-1]['daily_avg'] if full_weeks else list(weekly.values())[-1]['daily_avg']
    weekly_growth_pct = 5.0
    buffer_target_pct = 95

    cells = []

    # === HEADER ===
    cells.append(('Sheet1!A1', 'AKSHAYA — HQ Inventory Forecast'))
    cells.append(('Sheet1!A2', f'Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}'))

    # === CONFIG ===
    cells.append(('Sheet1!A4', 'CONFIG'))
    cells.append(('Sheet1!A5', 'Weekly Growth Rate (%)'))
    cells.append(('Sheet1!B5', '5'))
    cells.append(('Sheet1!C5', '← Expected WoW growth in order volume'))

    cells.append(('Sheet1!A6', 'Target % of Max Storage'))
    cells.append(('Sheet1!B6', '95'))
    cells.append(('Sheet1!C6', '← Order up to this % of max capacity'))

    cells.append(('Sheet1!A7', 'Event Week Start'))
    cells.append(('Sheet1!B7', '2026-05-04'))
    cells.append(('Sheet1!C7', '← Media Day 5/4 + Grand Opening 5/9'))

    cells.append(('Sheet1!A8', 'Event Bump (sustained %)'))
    cells.append(('Sheet1!B8', '50'))
    cells.append(('Sheet1!C8', '← +50% baseline lift from event week onward'))

    cells.append(('Sheet1!A9', 'Current Daily Orders'))
    cells.append(('Sheet1!B9', f'{current_daily_avg:.1f}'))
    cells.append(('Sheet1!C9', f'← Latest week avg (Square data through {max(daily_orders.keys())})'))

    # === WEEKLY ORDERS ===
    cells.append(('Sheet1!A11', 'WEEKLY ORDER VOLUME (from Square)'))
    cells.append(('Sheet1!A12', 'Week Starting'))
    cells.append(('Sheet1!B12', 'Days'))
    cells.append(('Sheet1!C12', 'Total Orders'))
    cells.append(('Sheet1!D12', 'Daily Avg'))
    cells.append(('Sheet1!E12', 'WoW Growth'))

    row = 13
    prev_avg = None
    for week_key, info in sorted(weekly.items()):
        avg = info['daily_avg']
        growth = ((avg / prev_avg) - 1) * 100 if prev_avg else None
        cells.append((f'Sheet1!A{row}', week_key))
        cells.append((f'Sheet1!B{row}', str(info['days'])))
        cells.append((f'Sheet1!C{row}', f'{info["total"]:.0f}'))
        cells.append((f'Sheet1!D{row}', f'{avg:.1f}'))
        cells.append((f'Sheet1!E{row}', f'{growth:+.1f}%' if growth is not None else 'N/A'))
        prev_avg = avg
        row += 1

    cells.append((f'Sheet1!A{row + 1}', f'Square data current through {max(daily_orders.keys())}; closing reports current through {latest_date}.'))

    # === HQ FORECAST ===
    forecast_start = row + 3
    cells.append((f'Sheet1!A{forecast_start}', 'HQ ITEM FORECAST — Order to 95% of Max'))
    cells.append((f'Sheet1!A{forecast_start + 1}', f'Closing inventory as of: {latest_date}   |   Event-aware days-of-supply (includes +50% lift from 5/4)'))

    headers = [
        'Item', 'Type', 'Max Cap', 'Target (95%)', 'Current Stock',
        'Avg Use/Day', 'Order Qty', 'Post-Order Stock', 'Days of Supply',
    ]
    header_row = forecast_start + 3
    for col_idx, h in enumerate(headers):
        col_letter = chr(65 + col_idx)
        cells.append((f'Sheet1!{col_letter}{header_row}', h))

    data_row = header_row + 1
    for item in HQ_ALL:
        cap = MAX_CAPACITY[item]
        target = cap * buffer_target_pct / 100
        stock = latest.get(item, 0) or 0
        use_per_day = consumption.get(item, 0)
        order_qty = max(0, target - stock)
        post_order = stock + order_qty
        dos = days_of_supply(post_order, use_per_day, base_daily_avg,
                             current_daily_avg, weekly_growth_pct)
        cat = 'Base' if item in HQ_BASES else 'Granola'

        cells.append((f'Sheet1!A{data_row}', item))
        cells.append((f'Sheet1!B{data_row}', cat))
        cells.append((f'Sheet1!C{data_row}', f'{cap:.1f}'))
        cells.append((f'Sheet1!D{data_row}', f'{target:.2f}'))
        cells.append((f'Sheet1!E{data_row}', f'{stock:.2f}'))
        cells.append((f'Sheet1!F{data_row}', f'{use_per_day:.2f}'))
        cells.append((f'Sheet1!G{data_row}', f'{order_qty:.2f}'))
        cells.append((f'Sheet1!H{data_row}', f'{post_order:.2f}'))
        cells.append((f'Sheet1!I{data_row}', f'{dos}' if dos < 365 else '365+'))
        data_row += 1

    # === NOTES ===
    notes_row = data_row + 2
    cells.append((f'Sheet1!A{notes_row}', 'NOTES'))
    note_lines = [
        '• Max Cap = soft opening inventory (3/23 channel message) — what we know fits in current storage.',
        '• Target = Max Cap × 95%. Order Qty = Target − Current Stock.',
        '• Avg Use/Day: positive-delta mean from ClickUp closing reports (restocks excluded).',
        '• Days of Supply: simulates daily consumption with 5% WoW growth + 50% event lift from 5/4.',
        '• ⚠ Items with DoS ≤ 3 (Coconut, Blade, GF Maple Hemp-Flax): max capacity likely under-stated OR need more frequent orders.',
        '• Phase 2: integrate Square recipes to sharpen per-item consumption; add Square API so order data refreshes automatically.',
    ]
    for i, line in enumerate(note_lines):
        cells.append((f'Sheet1!A{notes_row + 1 + i}', line))

    return cells


if __name__ == '__main__':
    cells = build_cells()
    out = Path(__file__).parent / 'sheet-data-v2.json'
    with open(out, 'w') as f:
        json.dump(cells, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(cells)} cells to {out}")
    max_row = max(int(''.join(x for x in ref.split('!')[1] if x.isdigit())) for ref, _ in cells)
    print(f"Max row: {max_row}")
    print("\nPreview of cells:")
    for ref, val in cells[:15]:
        print(f"  {ref}: {val}")
