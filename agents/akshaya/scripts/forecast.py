#!/usr/bin/env python3
# Legacy — replaced by forecast_v2.py

"""
AKSHAYA Inventory Forecasting — Phase 1: Correlation-based

Correlates Square order volume with ClickUp daily inventory counts
to estimate consumption rates and project days-of-supply.

No recipe decomposition — purely statistical correlation between
orders sold and inventory depleted.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
KNOWLEDGE_BASE = WORKSPACE / "agents" / "akshaya" / "knowledge-base"
GET_OPEN = WORKSPACE.parent / "get open"


# --- Inventory Parsing (ClickUp free-text → numeric) ---

def parse_inventory_value(raw: str) -> float | None:
    """Parse free-text inventory values like '23+60%' into a numeric estimate.

    Patterns seen in ClickUp data:
      - '23+60%'       → 23.6  (23 full packs + one at 60%)
      - '11'           → 11.0  (just a count)
      - '75%'          → 0.75  (percentage of one unit)
      - '3 boxes+30%'  → 3.3   (3 boxes + one at 30%)
      - '1 cambro+ 3 pouches' → skip (non-numeric packaging)
      - 'N/A', 'Na'    → None
    """
    if not raw or not isinstance(raw, str):
        return None

    raw = raw.strip()
    if raw.lower() in ('n/a', 'na', '-', 'o', ''):
        return None

    total = 0.0
    found_number = False

    number_pct_pattern = re.findall(r'(\d+\.?\d*)\s*(%)?', raw)
    for num_str, is_pct in number_pct_pattern:
        val = float(num_str)
        if is_pct:
            total += val / 100.0
        else:
            total += val
        found_number = True

    return total if found_number else None


def load_clickup_inventory(path: Path) -> dict[str, dict[str, float | None]]:
    """Load ClickUp Form Submission data → {date_str: {ingredient: numeric_value}}"""
    with open(path) as f:
        data = json.load(f)

    skip_fields = {
        'Manual LOW notes', 'Manual OUT notes', 'Shift notes',
        'Tips', 'Upload Deep Clean photos', 'Summary',
    }

    inventory_by_date = {}
    for task in data['data']:
        date_str = task['name'].replace('Form Submission - #', '')[:10]
        counts = {}
        for cf in task.get('custom_fields', []):
            name = cf['name']
            if name in skip_fields:
                continue
            val = cf.get('value')
            if isinstance(val, list):
                continue
            counts[name] = parse_inventory_value(str(val) if val else '')
        inventory_by_date[date_str] = counts

    return inventory_by_date


def compute_daily_depletion(inventory: dict) -> dict[str, dict[str, float | None]]:
    """Compute day-over-day inventory change (negative = consumption)."""
    dates = sorted(inventory.keys())
    depletion = {}
    for i in range(1, len(dates)):
        prev_date, curr_date = dates[i - 1], dates[i]
        prev_counts = inventory[prev_date]
        curr_counts = inventory[curr_date]

        daily = {}
        all_ingredients = set(prev_counts.keys()) | set(curr_counts.keys())
        for ing in all_ingredients:
            prev_val = prev_counts.get(ing)
            curr_val = curr_counts.get(ing)
            if prev_val is not None and curr_val is not None:
                daily[ing] = curr_val - prev_val
            else:
                daily[ing] = None
        depletion[curr_date] = daily

    return depletion


# --- Order Aggregation (Square CSV) ---

def load_square_orders(path: Path) -> dict[str, dict]:
    """Load Square items CSV → {date_str: {total_orders, by_category, by_size, by_item}}"""
    orders_by_date = defaultdict(lambda: {
        'total_qty': 0,
        'total_gross': 0.0,
        'by_category': defaultdict(int),
        'by_size': defaultdict(int),
        'by_item': defaultdict(int),
    })

    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row.get('Date', '').strip()
            if not date:
                continue

            qty = float(row.get('Qty', '0') or '0')
            gross = row.get('Gross Sales', '$0').replace('$', '').replace(',', '')
            try:
                gross = float(gross)
            except ValueError:
                gross = 0.0

            category = row.get('Category', '').strip()
            item = row.get('Item', '').strip()
            size = row.get('Price Point Name', '').strip()

            day = orders_by_date[date]
            day['total_qty'] += qty
            day['total_gross'] += gross
            if category:
                day['by_category'][category] += int(qty)
            if size:
                day['by_size'][size] += int(qty)
            if item:
                day['by_item'][item] += int(qty)

    return dict(orders_by_date)


# --- Correlation Analysis ---

def correlate_orders_inventory(
    orders: dict, depletion: dict
) -> dict[str, dict]:
    """For each ingredient, compute average daily depletion per order."""
    overlapping_dates = sorted(set(orders.keys()) & set(depletion.keys()))

    if not overlapping_dates:
        return {}

    ingredient_stats = defaultdict(lambda: {
        'depletion_values': [],
        'order_counts': [],
        'days': 0,
    })

    for date in overlapping_dates:
        total_orders = orders[date]['total_qty']
        if total_orders == 0:
            continue

        for ing, change in depletion[date].items():
            if change is None:
                continue
            stats = ingredient_stats[ing]
            stats['depletion_values'].append(change)
            stats['order_counts'].append(total_orders)
            stats['days'] += 1

    results = {}
    for ing, stats in ingredient_stats.items():
        if stats['days'] < 3:
            continue

        avg_daily_depletion = sum(stats['depletion_values']) / stats['days']
        avg_daily_orders = sum(stats['order_counts']) / stats['days']
        depletion_per_order = (
            avg_daily_depletion / avg_daily_orders if avg_daily_orders else 0
        )

        results[ing] = {
            'avg_daily_depletion': round(avg_daily_depletion, 2),
            'avg_daily_orders': round(avg_daily_orders, 1),
            'depletion_per_order': round(depletion_per_order, 3),
            'data_days': stats['days'],
        }

    return results


# --- Forecasting ---

def forecast_days_of_supply(
    latest_inventory: dict[str, float | None],
    correlation: dict[str, dict],
    target_daily_orders: float = 42.0,
) -> list[dict]:
    """Project days-of-supply for each ingredient at a target order rate."""
    forecasts = []

    for ing, current_stock in latest_inventory.items():
        if current_stock is None or current_stock <= 0:
            continue

        corr = correlation.get(ing)
        if not corr:
            continue

        daily_consumption = abs(corr['avg_daily_depletion'])
        if daily_consumption < 0.01:
            continue

        scaled_consumption = daily_consumption * (
            target_daily_orders / corr['avg_daily_orders']
        ) if corr['avg_daily_orders'] > 0 else daily_consumption

        days_supply = current_stock / scaled_consumption if scaled_consumption > 0 else 999

        forecasts.append({
            'ingredient': ing,
            'current_stock': round(current_stock, 1),
            'avg_daily_consumption': round(daily_consumption, 2),
            'scaled_daily_consumption': round(scaled_consumption, 2),
            'days_of_supply': round(days_supply, 1),
            'reorder_urgency': (
                'CRITICAL' if days_supply < 2 else
                'ORDER NOW' if days_supply < 4 else
                'ORDER SOON' if days_supply < 7 else
                'OK'
            ),
            'data_confidence': corr['data_days'],
        })

    forecasts.sort(key=lambda x: x['days_of_supply'])
    return forecasts


# --- Main ---

def main():
    print("=" * 70)
    print("AKSHAYA — Inventory Forecast (Phase 1: Correlation-based)")
    print("=" * 70)

    items_csv = GET_OPEN / "playground" / "items-2026-03-23-2026-04-12.csv"
    clickup_json = KNOWLEDGE_BASE / "clickup-inventory-raw.json"

    if not items_csv.exists():
        print(f"ERROR: Items CSV not found at {items_csv}")
        sys.exit(1)
    if not clickup_json.exists():
        print(f"ERROR: ClickUp inventory not found at {clickup_json}")
        sys.exit(1)

    # Load data
    print("\n1. Loading Square order data...")
    orders = load_square_orders(items_csv)
    order_dates = sorted(orders.keys())
    total_orders = sum(d['total_qty'] for d in orders.values())
    print(f"   {len(order_dates)} days, {int(total_orders)} items sold")
    print(f"   Range: {order_dates[0]} to {order_dates[-1]}")

    avg_daily = total_orders / len(order_dates) if order_dates else 0
    print(f"   Avg: {avg_daily:.1f} items/day")

    print("\n2. Loading ClickUp inventory data...")
    inventory = load_clickup_inventory(clickup_json)
    inv_dates = sorted(inventory.keys())
    print(f"   {len(inv_dates)} days of inventory counts")
    print(f"   Range: {inv_dates[0]} to {inv_dates[-1]}")

    ingredients_tracked = set()
    for counts in inventory.values():
        ingredients_tracked.update(k for k, v in counts.items() if v is not None)
    print(f"   {len(ingredients_tracked)} unique ingredients tracked")

    # Compute depletion
    print("\n3. Computing daily inventory depletion...")
    depletion = compute_daily_depletion(inventory)
    print(f"   {len(depletion)} days of depletion data")

    # Correlate
    print("\n4. Correlating orders → inventory consumption...")
    overlap = set(orders.keys()) & set(depletion.keys())
    print(f"   {len(overlap)} overlapping days")

    correlation = correlate_orders_inventory(orders, depletion)
    print(f"   {len(correlation)} ingredients with correlation data")

    # Show top consumers
    print("\n   Top daily consumers (avg units depleted/day):")
    sorted_corr = sorted(
        correlation.items(),
        key=lambda x: abs(x[1]['avg_daily_depletion']),
        reverse=True,
    )
    for ing, stats in sorted_corr[:15]:
        dep = stats['avg_daily_depletion']
        print(f"   {ing:30s}  {dep:+.2f}/day  ({stats['data_days']} days data)")

    # Forecast
    print("\n5. Forecasting days-of-supply (at current ~42 orders/day)...")
    latest_date = inv_dates[-1]
    latest = inventory[latest_date]
    print(f"   Using latest inventory from: {latest_date}")

    forecasts = forecast_days_of_supply(latest, correlation, target_daily_orders=42.0)

    print(f"\n{'INGREDIENT':30s} {'STOCK':>8s} {'USE/DAY':>8s} {'DAYS LEFT':>10s} {'STATUS':>12s}")
    print("-" * 75)
    for f in forecasts:
        print(
            f"{f['ingredient']:30s} "
            f"{f['current_stock']:8.1f} "
            f"{f['scaled_daily_consumption']:8.2f} "
            f"{f['days_of_supply']:10.1f} "
            f"{f['reorder_urgency']:>12s}"
        )

    # Scale forecast to target volumes
    print("\n6. Projection at target volume ($4K weekday = ~100 orders/day)...")
    forecasts_scaled = forecast_days_of_supply(latest, correlation, target_daily_orders=100.0)
    print(f"\n{'INGREDIENT':30s} {'STOCK':>8s} {'USE/DAY':>8s} {'DAYS LEFT':>10s} {'STATUS':>12s}")
    print("-" * 75)
    for f in forecasts_scaled:
        if f['days_of_supply'] < 14:
            print(
                f"{f['ingredient']:30s} "
                f"{f['current_stock']:8.1f} "
                f"{f['scaled_daily_consumption']:8.2f} "
                f"{f['days_of_supply']:10.1f} "
                f"{f['reorder_urgency']:>12s}"
            )

    # Save results
    output_path = KNOWLEDGE_BASE / "forecast-latest.json"
    output = {
        'generated': datetime.now().isoformat(),
        'latest_inventory_date': latest_date,
        'order_data_range': f"{order_dates[0]} to {order_dates[-1]}",
        'overlapping_days': len(overlap),
        'avg_daily_orders': round(avg_daily, 1),
        'correlation': {k: v for k, v in sorted_corr},
        'forecast_current_rate': forecasts,
        'forecast_target_rate': forecasts_scaled,
    }
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == '__main__':
    main()
