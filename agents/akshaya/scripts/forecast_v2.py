#!/usr/bin/env python3
"""
AKSHAYA Inventory Forecast — v2

HQ-only focus (bases + granolas).

Model (v1.6 refactor 2026-05-12, post-event):
  - Daily orders: last full-week avg from Square
  - Weekly growth: DERIVED from trailing geometric-mean of the last N weeks
    (default N=3). Configurable via the sheet's B7 cell. The old static "5%
    WoW + event bump" model was dropped once Media Day (5/4) and Grand Opening
    (5/9) passed — the trailing window naturally absorbs whatever the new
    steady-state lift looks like.
  - Initial Inventory (per item): re-anchored to the closing report on the
    Sunday immediately BEFORE the trend window starts. So with N=3 and a
    Wed-5/12 snapshot, the trend window is the 3 full weeks 4/20→5/10, and
    Initial Inventory = closing on 4/19. This makes "Target = SUM(B) × B6%"
    mean "stock to X% of where we were when the trend started measuring."
  - Per-HQ-item consumption rate: sum of downward-only moves over the last
    14 days / 14. Restock-robust (see compute_per_item_consumption docstring).

Outputs:
  - Order quantity to bring per-item stock toward target share
  - Resulting stock level after order
  - Days of supply post-order under the smoothed-trailing-growth model
"""

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
KB = WORKSPACE / "agents" / "akshaya" / "knowledge-base"
GET_OPEN = WORKSPACE.parent / "get open"

# --- Day-1 reference inventory (3/23 first ClickUp channel message) ---
#
# REPURPOSED 2026-05-12 (v1.6): no longer the primary anchor for B28:B36. The
# new model anchors Initial Inventory to the closing report at "window_start − 1"
# (i.e. the Sunday before the trend window starts). This dict is the FALLBACK
# used only when no closing report exists at or before the anchor date, AND it
# defines the canonical item set (HQ_BASES + granolas) for parsing closings.
DAY1_REFERENCE_INVENTORY = {
    'Açaí':              39.0,
    'Coconut':           17.0,
    'Tropical':          15.0,
    'Mango':             17.0,
    'Pitaya':            17.0,
    'Matcha':             5.0,
    'Ube':                4.0,
    'Pog':                4.0,
    'Blade':              4.0,
    'Honey Almond':       5.70,
    'GF Maple Hemp-Flax': 4.70,
    'Choco-Churro':       3.70,
    'Roasted Coffee':     3.70,
}
# Back-compat alias — many tests + downstream callers still import INITIAL_INVENTORY.
# Now resolves to the day-1 reference (still useful as a fallback / item-list source).
INITIAL_INVENTORY = DAY1_REFERENCE_INVENTORY
# HQ_BASES — items currently in the active allocation. Blade dropped 2026-05-12
# PM (v1.9) at user's request; still parseable via DAY1_REFERENCE_INVENTORY for
# historical closing-report compatibility. To re-include, add 'Blade' back here.
HQ_BASES = ['Açaí', 'Coconut', 'Tropical', 'Mango', 'Pitaya', 'Matcha', 'Ube', 'Pog']
HQ_GRANOLAS = ['Honey Almond', 'GF Maple Hemp-Flax', 'Choco-Churro', 'Roasted Coffee']

# Trailing-window growth rate config. With N=3 and weekly data
# [w_{T-2}, w_{T-1}, w_T] the rate is the geometric per-week growth
# `(w_T / w_{T-2}) ^ (1/2) − 1` expressed as %. N=3 chosen to smooth the
# Media Day 5/4 spike (one of the three weeks) while still reacting fast.
GROWTH_WINDOW_WEEKS_DEFAULT = 3


# --- Closing-report data corrections (v1.10, 2026-05-12) ---
# Manual data-entry errors in the ClickUp closing reports. Truck day is 4/30
# for all items; any other "restock" is a counter wobble or a typo. Overlay
# applied in load_inventory_timeseries() right after parse, leaves source
# (ClickUp tasks) untouched. To revert a correction, delete the entry. To add
# one, append (YYYY-MM-DD, item) -> corrected_value. value=None deletes.
CLOSING_REPORT_CORRECTIONS = {
    ('2026-04-28', 'Açaí'):    13.20,   # was 14.60; +1.10 wobble
    ('2026-04-30', 'Açaí'):    41.30,   # was 11.00; truck count late-captured 5/1
    ('2026-05-03', 'Açaí'):    39.50,   # was 41.55; +1.54 wobble
    ('2026-05-11', 'Açaí'):    None,    # was 37.30 then 32.50; user confirms today's C=24.5 is truth, 32.5 is bad data

    # Açaí 5/5-5/10 cluster — entire week of inflated readings (~+8u bias
    # relative to today's C=24.5-implied trajectory). Closer was likely
    # counting an extra Açaí batch (granola? separate fridge?). Dropped per
    # user direction "incorrect data points should be ignored" (2026-05-12 PM).
    # Rate now derives from 4/27-5/4 trusted data only via series-fallback.
    ('2026-05-05', 'Açaí'):    None,
    ('2026-05-06', 'Açaí'):    None,
    ('2026-05-07', 'Açaí'):    None,
    ('2026-05-08', 'Açaí'):    None,
    ('2026-05-09', 'Açaí'):    None,
    ('2026-05-10', 'Açaí'):    None,
    ('2026-04-29', 'Mango'):    7.50,   # was 9.90; +1.80 wobble
    ('2026-05-04', 'Mango'):   17.99,   # was 7.99; missing leading "1" typo
    ('2026-05-03', 'Ube'):      4.70,   # was 5.80; +1.00 wobble
    ('2026-05-05', 'Ube'):      4.60,   # was 5.80; +1.05 wobble
    ('2026-05-01', 'Pog'):      5.00,   # was 6.25; +1.25 wobble (truck was 4/30)

    # Pog 5/5-5/11 cluster — entire week of bad readings (1.80-1.99).
    # Today's C=5.80 contradicts these values per the "C is truth" principle.
    # Closer was likely measuring a near-empty residual or separate small
    # batch, not the full Pog inventory. Drop the readings; rate derives
    # from 4/27-5/4 trusted data via the series-fallback path.
    ('2026-05-05', 'Pog'):      None,
    ('2026-05-06', 'Pog'):      None,
    ('2026-05-07', 'Pog'):      None,
    ('2026-05-08', 'Pog'):      None,
    ('2026-05-09', 'Pog'):      None,
    ('2026-05-10', 'Pog'):      None,
    ('2026-05-11', 'Pog'):      None,
}


def parse_inv(raw):
    """Parse ClickUp inventory free-text like '23+60%' or '3 boxes+30%'.

    Convention: '<whole_units>+<fraction_pct>%'. The '%' is sometimes mistyped
    (e.g. '15+98^' — shift-6 instead of shift-5). Any number that appears AFTER
    a '+' is treated as a percentage, regardless of whether '%' is present.
    """
    if raw is None or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw.lower() in ('n/a', 'na', '-', 'o', ''):
        return None
    parts = re.split(r'[+,]', raw)
    total = 0.0
    found = False
    for idx, part in enumerate(parts):
        nums = re.findall(r'(\d+\.?\d*)', part)
        if not nums:
            continue
        val = float(nums[0])
        if idx == 0:
            has_pct = '%' in part
            total += val / 100 if has_pct else val
        else:
            total += val / 100  # post-'+' value is always a percentage
        found = True
    return total if found else None


def load_inventory_timeseries():
    """Load ClickUp closing reports into a {date: {item: float}} map.

    REWRITE 2026-05-12: now reads ONLY the freshly-refreshed snapshot. Per the
    fresh-fetch invariant (refresh-procedure.md § 0), `clickup-inventory-latest.json`
    is overwritten on every refresh with the full window from `search_tasks`,
    so a separate `clickup-inventory-raw.json` merge is no longer needed.

    If you're refreshing data, run the ClickUp MCP search FIRST and overwrite
    the file; don't read this without doing so or you'll be working with stale
    data from the prior session.
    """
    path = KB / 'clickup-inventory-latest.json'
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. Refresh first: call user-clickup search_tasks with "
            f"tag='closing submission' and overwrite this file. See "
            f"agents/akshaya/knowledge-base/refresh-procedure.md § 0."
        )
    with open(path) as f:
        data = json.load(f)
    fetched_at = data.get('fetched_at', 'unknown')
    inventory = {}
    tasks = data.get('data', [])
    if isinstance(tasks, dict):
        tasks = [tasks]
    for task in tasks:
        name = task.get('name', '')
        if not name.startswith('Form Submission'):
            continue
        date_str = name.replace('Form Submission - #', '')[:10]
        counts = {}
        for cf in task.get('custom_fields', []):
            item_name = cf.get('name', '').strip()
            # EXACT match only (avoids 'Coconut' matching 'Coconut milk cartons')
            if item_name in INITIAL_INVENTORY:
                v = cf.get('value')
                if isinstance(v, list):
                    continue
                parsed = parse_inv(str(v) if v is not None else '')
                if parsed is not None:
                    counts[item_name] = parsed
        if counts:
            inventory[date_str] = counts
    print(f"  [load_inventory_timeseries] {len(inventory)} reports loaded from snapshot fetched_at={fetched_at}")

    applied = 0
    for (date_str, item), corrected_value in CLOSING_REPORT_CORRECTIONS.items():
        if date_str not in inventory:
            continue
        if corrected_value is None:
            if item in inventory[date_str]:
                del inventory[date_str][item]
                applied += 1
        else:
            inventory[date_str][item] = corrected_value
            applied += 1
    if applied:
        print(f"  [load_inventory_timeseries] applied {applied} closing-report corrections from CLOSING_REPORT_CORRECTIONS overlay")
    return inventory


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def compute_per_item_consumption(inventory, rate_window_days=14,
                                 noise_floor=0.3):
    """Per-item avg daily consumption via DOWNWARD-ONLY moves in a recent window.

    REWRITE 2026-05-12: the prior "(initial − current) / days_elapsed" estimator
    assumed no restocks since opening day. ClickUp data through 5/11 confirmed
    that's broken for every base — at least one HQ shipment landed for each
    item between 4/22 and 5/11, with several items getting multiple restocks.

    New estimator (restock-robust):
        For each consecutive pair of closing-report dates in the window:
            consumed[i] = max(0, value[i-1] - value[i])    # ignore upward jumps
        rate_per_day = sum(consumed) / window_days

    Why downward-only:
        - A restock is a positive jump; contributes 0 to consumption sum.
          Multiple restocks → still works, each ignored separately.
        - Real consumption is the daily decrease; captured fully.
        - Robust to typos that go up then back down (the corrective drop is
          counted, which slightly over-counts, but the magnitude is tiny vs
          real consumption signal).

    Why a window (not full history):
        - Demand changes (events, growth). 14 days is long enough to smooth
          weekly weekend bumps, short enough to react to a sustained shift
          (e.g. Grand Opening post-5/9 lift).
        - The previous full-window estimator dampened recent surges.

    Current stock:
        Still the raw latest closing report value. No more denoising — restocks
        make "monotone decrease" invalidating, so the previous typo-detection
        rule can't fire. Users should manually override C-column if a specific
        report looks clearly typo'd; we don't auto-correct.

    Restock detection (informational, surfaced in output):
        Any pair where value[i] - value[i-1] > 1.0 is flagged as a probable
        restock. Threshold > noise_floor (default 0.3) per individual upward
        drift to avoid false positives from rounding/typos.

    Returns per item: {
        'rate': float,                    # daily consumption rate
        'current_stock': float|None,      # raw latest closing report
        'current_stock_source': 'latest'|'missing',
        'initial_inventory': float,       # day-1 stock (REFERENCE ONLY now — not used in rate calc)
        'window_days': int,               # window used for rate
        'window_first_date': str,
        'window_last_date': str,
        'first_date': str,                # first closing report overall
        'last_date': str,                 # latest closing report
        'total_downward_in_window': float,
        'restocks_detected': list,        # [{date_from, date_to, delta, prev, cur}]
        'noisy': bool,                    # True if rate is 0 or data is sparse
        'noisy_reason': str|None,
    }
    """
    consumption = {}
    dates = sorted(inventory.keys())
    if not dates:
        return consumption

    first_date = dates[0]
    last_date = dates[-1]
    last_dt = datetime.strptime(last_date, '%Y-%m-%d')
    window_start_dt = last_dt - timedelta(days=rate_window_days)
    window_dates = [d for d in dates
                    if datetime.strptime(d, '%Y-%m-%d') >= window_start_dt]
    raw_latest_row = inventory[last_date]

    for item, initial in INITIAL_INVENTORY.items():
        series = [(d, inventory[d][item]) for d in window_dates if item in inventory[d]]
        raw_latest = raw_latest_row.get(item)

        # v1.10 patch: if the latest snapshot date has the item missing
        # (e.g. CLOSING_REPORT_CORRECTIONS dropped a bad-tail cluster ending
        # at the snapshot edge), fall back to the latest available reading
        # in the in-window series. Without this, deleting a bad tail like
        # Pog 5/5-5/11 makes rate=0 even with valid earlier data.
        latest_source = 'latest'
        latest_reason_override = None
        if raw_latest is None and series:
            fallback_date, fallback_val = series[-1]
            raw_latest = fallback_val
            latest_source = 'series-fallback'
            latest_reason_override = (
                f'snapshot {last_date} had no {item} reading '
                f'(overlay-dropped); using in-window value from {fallback_date}'
            )

        base = {
            'initial_inventory': initial,
            'window_days': rate_window_days,
            'window_first_date': series[0][0] if series else None,
            'window_last_date': series[-1][0] if series else None,
            'first_date': first_date,
            'last_date': last_date,
            'raw_latest': round(raw_latest, 2) if raw_latest is not None else None,
        }

        # Backwards-compat keys (denoised_median, current_stock_reason)
        # are filled below; old fields kept None since v2 doesn't denoise.
        compat = {'denoised_median': None}

        if raw_latest is None:
            consumption[item] = {**base, **compat, 'rate': 0.0, 'current_stock': None,
                                 'current_stock_source': 'missing',
                                 'current_stock_reason': 'no closing report on latest date',
                                 'total_downward_in_window': 0.0,
                                 'restocks_detected': [],
                                 'noisy': True,
                                 'noisy_reason': 'no closing report on latest date'}
            continue

        if len(series) < 2:
            consumption[item] = {**base, **compat, 'rate': 0.0,
                                 'current_stock': round(raw_latest, 2),
                                 'current_stock_source': 'latest',
                                 'current_stock_reason': f'raw latest from {last_date}',
                                 'total_downward_in_window': 0.0,
                                 'restocks_detected': [],
                                 'noisy': True,
                                 'noisy_reason': f'only {len(series)} report(s) in {rate_window_days}d window — need ≥2 for rate'}
            continue

        # Walk consecutive pairs, summing downward moves and flagging restocks.
        total_down = 0.0
        restocks = []
        for i in range(1, len(series)):
            prev_date, prev_val = series[i-1]
            cur_date, cur_val = series[i]
            delta = cur_val - prev_val
            if delta < -noise_floor:
                total_down += -delta  # consumption
            elif delta > 1.0:
                restocks.append({
                    'date_from': prev_date, 'date_to': cur_date,
                    'prev': round(prev_val, 2), 'cur': round(cur_val, 2),
                    'delta': round(delta, 2),
                })

        rate = total_down / rate_window_days
        noisy = rate <= 0.001
        reason = (f'rate from {total_down:.1f}u downward consumption over '
                  f'{rate_window_days}d window ({series[0][0]}→{series[-1][0]}, '
                  f'{len(restocks)} restock(s) detected)')
        consumption[item] = {**base, **compat,
                             'rate': round(rate, 3),
                             'current_stock': round(raw_latest, 2),
                             'current_stock_source': latest_source,
                             'current_stock_reason': (latest_reason_override
                                                      if latest_reason_override
                                                      else reason),
                             'total_downward_in_window': round(total_down, 2),
                             'restocks_detected': restocks,
                             'noisy': noisy,
                             'noisy_reason': ('no downward moves in window — '
                                              'item may be untracked or fully restocked'
                                              if noisy else None)}
    return consumption


def load_square_orders():
    """Sum daily item-level Qty across all Square CSV exports in playground/.

    Dedups by Transaction ID + Token + Item to handle overlapping export ranges
    (e.g., an export covering 3/23-4/12 plus a second export covering 4/1-4/21).
    """
    daily = defaultdict(float)
    seen = set()
    csv_dir = GET_OPEN / "playground"
    for csv_path in sorted(csv_dir.glob("items-*.csv")):
        with open(csv_path, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                d = row.get('Date', '').strip()
                if not d:
                    continue
                dedup_key = (row.get('Transaction ID', ''), row.get('Token', ''),
                             row.get('Item', ''), row.get('Qty', ''), row.get('Time', ''))
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                q = float(row.get('Qty', '0') or '0')
                daily[d] += q
    return dict(daily)


def _weekly(daily_orders):
    buckets = defaultdict(list)
    for d, q in daily_orders.items():
        dt = datetime.strptime(d, '%Y-%m-%d')
        ws = dt - timedelta(days=dt.weekday())
        buckets[ws.strftime('%Y-%m-%d')].append(q)
    return {w: {'daily_avg': sum(qs)/len(qs), 'total': sum(qs), 'days': len(qs)}
            for w, qs in sorted(buckets.items())}


def compute_trailing_growth_rate(weekly, n_weeks=GROWTH_WINDOW_WEEKS_DEFAULT):
    """Geometric-mean weekly growth rate over the last `n_weeks` full weeks.

    Formula:  (w_last / w_first) ^ (1 / (N-1))  − 1,  expressed as % per week.
    Returns dict: {
        'rate_pct': float,            # % per week (e.g. 28.3 means +28.3%/wk)
        'n_weeks_used': int,          # may be < n_weeks if data is short
        'window_start_week': str,     # 'YYYY-MM-DD' Monday of first week in window
        'window_end_week': str,       # 'YYYY-MM-DD' Monday of last week in window
        'first_daily_avg': float,
        'last_daily_avg': float,
        'fallback_reason': str|None,  # set when n_weeks_used < n_weeks requested
    }

    Edge cases:
      - n_weeks < 2: returns rate 0 (can't compute growth from a single point)
      - len(full_weeks) < n_weeks: falls back to all available full weeks
        and surfaces a fallback_reason
      - len(full_weeks) < 2 after fallback: returns rate 0
      - first_daily_avg == 0: returns rate 0 (avoid div-by-zero)
    """
    full_weeks = [(k, v) for k, v in sorted(weekly.items()) if v['days'] == 7]
    fallback_reason = None
    if n_weeks < 2:
        return {'rate_pct': 0.0, 'n_weeks_used': max(0, len(full_weeks)),
                'window_start_week': full_weeks[0][0] if full_weeks else None,
                'window_end_week': full_weeks[-1][0] if full_weeks else None,
                'first_daily_avg': None, 'last_daily_avg': None,
                'fallback_reason': 'n_weeks must be ≥ 2 to compute growth'}
    if len(full_weeks) < 2:
        return {'rate_pct': 0.0, 'n_weeks_used': len(full_weeks),
                'window_start_week': full_weeks[0][0] if full_weeks else None,
                'window_end_week': full_weeks[-1][0] if full_weeks else None,
                'first_daily_avg': None, 'last_daily_avg': None,
                'fallback_reason': f'only {len(full_weeks)} full week(s) of data — need ≥ 2'}
    if len(full_weeks) < n_weeks:
        fallback_reason = (f'requested {n_weeks}-week window but only '
                           f'{len(full_weeks)} full weeks of data available; '
                           f'falling back to N={len(full_weeks)}')
        n_used = len(full_weeks)
    else:
        n_used = n_weeks

    window = full_weeks[-n_used:]
    first_avg = window[0][1]['daily_avg']
    last_avg = window[-1][1]['daily_avg']
    if first_avg <= 0:
        return {'rate_pct': 0.0, 'n_weeks_used': n_used,
                'window_start_week': window[0][0],
                'window_end_week': window[-1][0],
                'first_daily_avg': first_avg, 'last_daily_avg': last_avg,
                'fallback_reason': 'first week in window had 0 daily orders'}
    ratio = last_avg / first_avg
    rate_per_week = ratio ** (1.0 / (n_used - 1)) - 1.0
    return {
        'rate_pct': round(rate_per_week * 100, 2),
        'n_weeks_used': n_used,
        'window_start_week': window[0][0],
        'window_end_week': window[-1][0],
        'first_daily_avg': round(first_avg, 2),
        'last_daily_avg': round(last_avg, 2),
        'fallback_reason': fallback_reason,
    }


def compute_window_start_anchor_date(weekly, n_weeks=GROWTH_WINDOW_WEEKS_DEFAULT):
    """The Sunday closing date IMMEDIATELY BEFORE the trend window begins.

    With N=3 and last full week starting Mon 2026-05-04, the window covers
    weeks starting 4/20, 4/27, 5/04 (Mondays). Window start = Monday 4/20.
    Anchor = the day before = Sunday 4/19. We use this date to look up
    per-item stock from the ClickUp closing report — that becomes the new
    "Initial Inventory" baseline (B28:B36 on the sheet).

    Returns date string 'YYYY-MM-DD' or None if no full weeks exist.
    """
    full_week_keys = sorted([k for k, v in weekly.items() if v['days'] == 7])
    if not full_week_keys:
        return None
    n_used = min(n_weeks, len(full_week_keys))
    window_start_monday = datetime.strptime(full_week_keys[-n_used], '%Y-%m-%d')
    anchor = window_start_monday - timedelta(days=1)  # Sunday before
    return anchor.strftime('%Y-%m-%d')


def resolve_inventory_at_anchor(inventory, anchor_date, items=None):
    """Look up the per-item stock at or BEFORE the anchor date.

    The ClickUp closing report cadence isn't perfectly daily — some days are
    missing. If we don't have a report exactly at anchor_date, fall back to the
    most recent earlier date with data. Returns:
        ({item: stock}, actual_date_used | None)
    actual_date_used is None ⇒ no closing report on or before the anchor,
    caller should fall back to DAY1_REFERENCE_INVENTORY (with a warning).
    """
    if items is None:
        items = list(DAY1_REFERENCE_INVENTORY.keys())
    if anchor_date is None:
        return ({}, None)
    candidate_dates = sorted(d for d in inventory.keys() if d <= anchor_date)
    if not candidate_dates:
        return ({}, None)
    actual = candidate_dates[-1]
    row = inventory[actual]
    return ({item: row[item] for item in items if item in row}, actual)


def days_of_supply(stock, item_base_consumption, base_daily_orders,
                   current_daily_orders, weekly_growth_pct, horizon_days=365):
    """How many days until `stock` hits zero under smoothed-trailing growth.

    v1.6 (2026-05-12): event-bump term removed. The trailing-window rate (B5
    on the sheet, derived from the last N weeks via geometric mean) already
    absorbs any sustained shift from prior events.
    """
    if item_base_consumption <= 0:
        return 999
    remaining = stock
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    daily_growth = (1 + weekly_growth_pct / 100) ** (1/7) - 1
    for d in range(horizon_days):
        growth_factor = (1 + daily_growth) ** d
        daily_orders = current_daily_orders * growth_factor
        scale = daily_orders / base_daily_orders if base_daily_orders > 0 else 1
        consumption_today = item_base_consumption * scale
        remaining -= consumption_today
        if remaining <= 0:
            return d
    return horizon_days  # Capped


def main():
    inventory = load_inventory_timeseries()
    dates = sorted(inventory.keys())
    print(f"Inventory data: {len(dates)} days, {dates[0]} → {dates[-1]}")

    latest = inventory[dates[-1]]
    consumption = compute_per_item_consumption(inventory)

    daily_orders = load_square_orders()
    weekly = _weekly(daily_orders)
    print(f"\nOrder data: {len(daily_orders)} days, weeks:")
    for w, info in weekly.items():
        print(f"  Week {w}: {info['days']}d, avg {info['daily_avg']:.1f}/day")

    # Base rate = average across all days on record
    base_daily_avg = sum(daily_orders.values()) / len(daily_orders)
    # Current = latest FULL week (7 days); if all weeks are partial, fall back to latest
    full_weeks = [w for w in weekly.values() if w['days'] == 7]
    current_daily_avg = full_weeks[-1]['daily_avg'] if full_weeks else list(weekly.values())[-1]['daily_avg']

    growth = compute_trailing_growth_rate(weekly, n_weeks=GROWTH_WINDOW_WEEKS_DEFAULT)
    weekly_growth_pct = growth['rate_pct']
    buffer_target_pct = 95
    anchor_date = compute_window_start_anchor_date(weekly, n_weeks=GROWTH_WINDOW_WEEKS_DEFAULT)
    anchored_inv, used_date = resolve_inventory_at_anchor(inventory, anchor_date)

    print(f"\nBaseline daily orders (historical avg): {base_daily_avg:.1f}")
    print(f"Current daily orders (latest week avg):  {current_daily_avg:.1f}")
    print(f"Trailing growth ({growth['n_weeks_used']}-week geo-mean):    {weekly_growth_pct:+.1f}%/week")
    if growth.get('fallback_reason'):
        print(f"  ↳ FALLBACK: {growth['fallback_reason']}")
    print(f"Initial Inventory anchor:                 {used_date or '— (day-1 fallback)'}\n")

    rows = []
    print(f"{'Item':22s} {'Type':8s} {'Init':>6s} {'Target':>7s} {'Raw':>6s} {'Med':>6s} "
          f"{'Stock':>6s} {'Src':>9s} {'Use/Day':>8s} {'Order':>7s} {'PostOrd':>8s} {'DoS':>5s}")
    print('-' * 115)

    for item in HQ_BASES + HQ_GRANOLAS:
        initial = anchored_inv.get(item, DAY1_REFERENCE_INVENTORY[item])
        target = initial * buffer_target_pct / 100
        cinfo = consumption.get(item, {'rate': 0, 'noisy': True,
                                        'current_stock': None,
                                        'current_stock_source': 'missing'})
        current = cinfo.get('current_stock')
        stock = current if current is not None else (latest.get(item, 0) or 0)
        raw_latest = cinfo.get('raw_latest')
        denoised_median = cinfo.get('denoised_median')
        source = cinfo.get('current_stock_source', 'missing')
        use_per_day = cinfo['rate']
        order_qty = max(0, target - stock)
        post_order = stock + order_qty
        dos = days_of_supply(post_order, use_per_day, base_daily_avg, current_daily_avg,
                              weekly_growth_pct)
        cat = 'Base' if item in HQ_BASES else 'Granola'

        rows.append({
            'item': item,
            'category': cat,
            'initial_inventory': initial,
            'target_95pct': round(target, 2),
            'current_stock': round(stock, 2),
            'current_stock_source': source,
            'current_stock_reason': cinfo.get('current_stock_reason', ''),
            'current_stock_raw_latest': raw_latest,
            'current_stock_denoised_median': denoised_median,
            'avg_use_per_day': round(use_per_day, 3),
            'avg_use_noisy': cinfo.get('noisy', False),
            'days_elapsed': cinfo.get('days_elapsed'),
            'denoise_window_size': cinfo.get('window_size'),
            'window_first_date': cinfo.get('window_first_date'),
            'window_last_date': cinfo.get('window_last_date'),
            'consumption_first_date': cinfo.get('first_date'),
            'consumption_last_date': cinfo.get('last_date'),
            'order_qty': round(order_qty, 2),
            'post_order_stock': round(post_order, 2),
            'days_of_supply_post_order': dos,
        })

        flag = ' ⚠NOISY' if cinfo.get('noisy') else ''
        raw_str = f"{raw_latest:6.2f}" if raw_latest is not None else f"{'—':>6s}"
        med_str = f"{denoised_median:6.2f}" if denoised_median is not None else f"{'—':>6s}"
        print(f"{item:22s} {cat:8s} {initial:6.1f} {target:7.2f} {raw_str} {med_str} "
              f"{stock:6.2f} {source:>9s} {use_per_day:8.3f} {order_qty:7.2f} "
              f"{post_order:8.2f} {dos:5d}{flag}")

    print("\n* Stock = raw latest by default; denoised (median of last 7 reports) "
          "only when raw > 30% + 0.5 units above median (implausible w/o restocks).")

    out = KB / "forecast-v2-latest.json"
    payload = {
        'generated_at': datetime.now().isoformat(),
        'inventory_dates': dates,
        'latest_inventory_date': dates[-1],
        'orders_date_range': [min(daily_orders.keys()), max(daily_orders.keys())],
        'base_daily_avg': round(base_daily_avg, 2),
        'current_daily_avg': round(current_daily_avg, 2),
        'weekly_growth_pct': weekly_growth_pct,
        'growth_window_weeks': growth['n_weeks_used'],
        'growth_window_start_week': growth.get('window_start_week'),
        'initial_inventory_anchor_date': used_date,
        'buffer_target_pct': buffer_target_pct,
        'weekly_order_volume': weekly,
        'rows': rows,
    }
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {out}")


if __name__ == '__main__':
    main()
