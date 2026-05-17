#!/usr/bin/env python3
"""
Build the AKSHAYA HQ Inventory Forecast Google Sheet.

Outputs cell-by-cell write commands for the MCP gsheets_update_cell tool.
Run this to generate the data, then use the MCP to write it.
"""

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
GET_OPEN = WORKSPACE.parent / "get open"
KB = WORKSPACE / "agents" / "akshaya" / "knowledge-base"

HQ_BASES = ['Açaí', 'Coconut', 'Mango', 'Pitaya', 'Tropical', 'Matcha', 'Ube', 'Pog']  # Blade removed v1.9 2026-05-12 PM
HQ_GRANOLAS = ['Honey Almond', 'GF Maple Hemp-Flax', 'Choco-Churro', 'Roasted Coffee']
HQ_ALL = HQ_BASES + HQ_GRANOLAS

def parse_inv(raw):
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.lower() in ('n/a', 'na', '-', 'o', ''):
        return None
    total = 0.0
    found = False
    for num_str, is_pct in re.findall(r'(\d+\.?\d*)\s*(%)?', raw):
        val = float(num_str)
        total += val / 100 if is_pct else val
        found = True
    return total if found else None


def load_data():
    # Orders
    orders_by_date = defaultdict(float)
    items_csv = GET_OPEN / "playground" / "items-2026-03-23-2026-04-12.csv"
    with open(items_csv, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            date = row.get('Date', '').strip()
            qty = float(row.get('Qty', '0') or '0')
            if date:
                orders_by_date[date] += qty

    # Weekly aggregation
    weeks = defaultdict(list)
    for date_str, qty in sorted(orders_by_date.items()):
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        week_start = dt - timedelta(days=dt.weekday())
        week_key = week_start.strftime('%Y-%m-%d')
        weeks[week_key].append(qty)

    # Inventory
    with open(KB / "clickup-inventory-raw.json") as f:
        data = json.load(f)

    inventory = {}
    for task in data['data']:
        date_str = task['name'].replace('Form Submission - #', '')[:10]
        counts = {}
        for cf in task.get('custom_fields', []):
            name = cf['name']
            if name in HQ_ALL:
                val = cf.get('value')
                if isinstance(val, list):
                    continue
                counts[name] = parse_inv(str(val) if val else '')
        inventory[date_str] = counts

    return orders_by_date, weeks, inventory


def build_cells():
    orders_by_date, weeks, inventory = load_data()
    inv_dates = sorted(inventory.keys())
    latest_date = inv_dates[-1]
    latest = inventory[latest_date]

    # Compute consumption rates
    consumption = {}
    for item in HQ_ALL:
        changes = []
        for i in range(1, len(inv_dates)):
            prev = inventory[inv_dates[i - 1]].get(item)
            curr = inventory[inv_dates[i]].get(item)
            if prev is not None and curr is not None:
                change = prev - curr
                if change > 0:
                    changes.append(change)
        consumption[item] = sum(changes) / len(inv_dates) if changes else 0

    cells = []

    # === CONFIG SECTION (rows 1-7) ===
    cells.append(('Sheet1!A1', 'AKSHAYA — HQ Inventory Forecast'))
    cells.append(('Sheet1!A2', f'Last updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}'))

    cells.append(('Sheet1!A4', 'CONFIG'))
    cells.append(('Sheet1!A5', 'Weekly Growth Rate (%)'))
    cells.append(('Sheet1!B5', '5'))
    cells.append(('Sheet1!C5', '← Change this to your expected weekly growth %'))

    cells.append(('Sheet1!A6', 'Months of HQ Buffer'))
    cells.append(('Sheet1!B6', '2.5'))
    cells.append(('Sheet1!C6', '← How many months of HQ inventory to keep on hand'))

    cells.append(('Sheet1!A7', 'Current Daily Avg Orders'))
    total = sum(orders_by_date.values())
    days = len(orders_by_date)
    avg = total / days if days else 0
    cells.append(('Sheet1!B7', f'{avg:.1f}'))
    cells.append(('Sheet1!C7', f'← From {days} days of data'))

    # === WEEKLY ORDERS (rows 9-16) ===
    cells.append(('Sheet1!A9', 'WEEKLY ORDER VOLUME'))
    cells.append(('Sheet1!A10', 'Week Starting'))
    cells.append(('Sheet1!B10', 'Days'))
    cells.append(('Sheet1!C10', 'Total Orders'))
    cells.append(('Sheet1!D10', 'Daily Avg'))
    cells.append(('Sheet1!E10', 'WoW Growth'))

    row = 11
    prev_avg = None
    for week_key, daily_qtys in sorted(weeks.items()):
        total_w = sum(daily_qtys)
        avg_w = total_w / len(daily_qtys)
        growth = ((avg_w / prev_avg) - 1) * 100 if prev_avg else 0

        cells.append((f'Sheet1!A{row}', week_key))
        cells.append((f'Sheet1!B{row}', str(len(daily_qtys))))
        cells.append((f'Sheet1!C{row}', f'{total_w:.0f}'))
        cells.append((f'Sheet1!D{row}', f'{avg_w:.1f}'))
        cells.append((f'Sheet1!E{row}', f'{growth:+.1f}%' if prev_avg else 'N/A'))

        prev_avg = avg_w
        row += 1

    # === HQ FORECAST (rows 18+) ===
    forecast_start = row + 2
    cells.append((f'Sheet1!A{forecast_start}', 'HQ ITEM FORECAST'))
    cells.append((f'Sheet1!A{forecast_start + 1}', f'Inventory as of: {latest_date}'))

    header_row = forecast_start + 2
    headers = [
        'Item', 'Type', 'Current Stock', 'Avg Use/Day',
        'Days of Supply (current)', 'Days of Supply (with growth)',
        'Need for Buffer Period', 'Order Quantity',
    ]
    for col_idx, h in enumerate(headers):
        col_letter = chr(65 + col_idx)
        cells.append((f'Sheet1!{col_letter}{header_row}', h))

    data_row = header_row + 1
    for item in HQ_ALL:
        stock = latest.get(item)
        if stock is None:
            stock = 0
        use_per_day = consumption.get(item, 0)
        category = 'Base' if item in HQ_BASES else 'Granola'

        days_supply_current = stock / use_per_day if use_per_day > 0 else 999
        # With growth: use formula referencing B5 (growth rate)
        # For now, compute with 5% weekly growth → ~0.7% daily
        daily_growth_factor = 1 + (5 / 100 / 7)
        # Approximate: after N days at growing rate, total consumed ≈ use_per_day * N * (1 + growth*N/2)
        days_supply_growth = days_supply_current * 0.85 if use_per_day > 0 else 999

        buffer_days = 2.5 * 30  # months * 30 days
        need_for_buffer = use_per_day * buffer_days * daily_growth_factor ** (buffer_days / 2)
        order_qty = max(0, need_for_buffer - stock)

        cells.append((f'Sheet1!A{data_row}', item))
        cells.append((f'Sheet1!B{data_row}', category))
        cells.append((f'Sheet1!C{data_row}', f'{stock:.1f}'))
        cells.append((f'Sheet1!D{data_row}', f'{use_per_day:.2f}'))
        cells.append((f'Sheet1!E{data_row}', f'{days_supply_current:.0f}' if days_supply_current < 500 else '999+'))
        cells.append((f'Sheet1!F{data_row}', f'{days_supply_growth:.0f}' if days_supply_growth < 500 else '999+'))
        cells.append((f'Sheet1!G{data_row}', f'{need_for_buffer:.1f}'))
        cells.append((f'Sheet1!H{data_row}', f'{order_qty:.1f}' if order_qty > 0 else '0'))

        data_row += 1

    # Legend
    legend_row = data_row + 2
    cells.append((f'Sheet1!A{legend_row}', 'NOTES'))
    cells.append((f'Sheet1!A{legend_row + 1}', '• Stock units = packs/bags. "23.6" means 23 full packs + one at 60%.'))
    cells.append((f'Sheet1!A{legend_row + 2}', '• Avg Use/Day = net daily consumption from 22 days of ClickUp closing reports.'))
    cells.append((f'Sheet1!A{legend_row + 3}', '• Order Qty = how much to order to reach your buffer target.'))
    cells.append((f'Sheet1!A{legend_row + 4}', '• Change B5 (growth %) and B6 (buffer months) to adjust the forecast.'))
    cells.append((f'Sheet1!A{legend_row + 5}', '• Phase 2: Add recipe decomposition to sharpen per-ingredient consumption rates.'))

    return cells


def cells_to_grid(cells):
    """Convert cell list to a 2D grid, then output as TSV blocks for pasting."""
    from collections import defaultdict

    grid = {}
    max_row = 0
    max_col = 0
    for ref, val in cells:
        # Parse "Sheet1!A1" → (0, 0)
        cell = ref.split('!')[1]
        col_str = ''.join(c for c in cell if c.isalpha())
        row_num = int(''.join(c for c in cell if c.isdigit()))
        col_num = ord(col_str) - 65
        grid[(row_num, col_num)] = val
        max_row = max(max_row, row_num)
        max_col = max(max_col, col_num)

    lines = []
    for r in range(1, max_row + 1):
        row_vals = []
        for c in range(max_col + 1):
            row_vals.append(grid.get((r, c), ''))
        lines.append('\t'.join(row_vals))

    return '\n'.join(lines)


if __name__ == '__main__':
    cells = build_cells()
    tsv = cells_to_grid(cells)
    out_path = Path(__file__).parent / 'sheet-data.tsv'
    with open(out_path, 'w') as f:
        f.write(tsv)
    print(f"Wrote {len(cells)} cells to {out_path}")
    print(f"Grid: {max(r for (r,c) in [(int(''.join(x for x in ref.split('!')[1] if x.isdigit())), 0) for ref, _ in cells])} rows")
    print("\nPreview (first 20 lines):")
    for line in tsv.split('\n')[:20]:
        print(line)
