#!/usr/bin/env python3
"""
Build AKSHAYA sheet v3 — BASES ONLY, fully formula-driven.

Design:
  - Static inputs per item: Initial Inventory, Current Stock, Avg Use/Day (cols B,C,D)
  - All derived values are formulas that reference config cells ($B$5-$B$9)
    so the user can edit target %, weekly growth, event date, event bump,
    or snapshot date and the whole table recomputes in-sheet.

Allocation (target-driven, proportional to Avg Use/Day, floor-clamped, Δ-overridable):
  TARGET_TOTAL = Total Initial Inventory × B6%
  prop_i       = TARGET_TOTAL × use_i/day / Σ(use_j/day)
  target_i     = MAX($B$12 floor, prop_i)       ← no per-item upper cap
  Δ_i          = user input in column G (default 0, +/- units)
  E_i          = MAX(0, CEILING(target_i − C_i + Δ_i, 1))  ← whole-unit order qty
  F_i          = ROUND(C_i + E_i, 2)            ← actual post-order stock

  Δ semantics: delta is applied AFTER the floor, so a negative Δ can intentionally
  take F below the B12 floor (explicit user override). Δ=0 keeps v1.2 behavior
  exactly (regression-protected by tests).

  Note: Initial Inventory (B) is day-1 stock, NOT a physical storage ceiling,
  so we do not clamp F above B. The minimum floor (B12) IS honored when Δ=0; tweak
  B6 down if the resulting total overshoots target without per-row tweaks.

Days of Supply (event-aware, all editable via config):
  days_pre = MAX(0, event_date - snapshot_date)
  pre_rate = use/day * (1 + growth%)
  post_rate = pre_rate * (1 + event_bump%)
  If stock covers pre-event burn: DoS = days_pre + (stock - pre_burn) / post_rate
  Else: DoS = stock / pre_rate
"""

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from forecast_v2 import (
    INITIAL_INVENTORY, DAY1_REFERENCE_INVENTORY, HQ_BASES,
    GROWTH_WINDOW_WEEKS_DEFAULT,
    load_inventory_timeseries, compute_per_item_consumption,
    load_square_orders, _weekly,
    compute_trailing_growth_rate, compute_window_start_anchor_date,
    resolve_inventory_at_anchor,
)

# Sheet row constants
# CONFIG grew from 7 rows (5–11) to 8 rows (5–12) with the addition of B12
# (Min Units per Base). Everything below shifted by +1 row accordingly.
CONFIG_START = 5          # rows 5–12
WEEKLY_HEADER_ROW = 13    # section title
WEEKLY_COLS_ROW = 14      # column headers
WEEKLY_DATA_START = 15    # data rows start
# WARNING_ROW / FORECAST_TITLE_ROW etc. are now *dynamic* — see build_updates()
# where they get recomputed based on the actual number of full weeks. The
# constants below remain only as default/baseline values for the legacy 6-week
# layout. Tests that referenced ITEM_HEADER_ROW=27 should keep passing because
# the dynamic offsets resolve to the same values when num_weeks ≤ 6.
WARNING_ROW = 22          # baseline (7 full weeks: 15..21 weekly data, 22 warning)
FORECAST_TITLE_ROW = 23
SUMMARY_ROW_1 = 24
SUMMARY_ROW_2 = 25
ITEM_HEADER_ROW = 27
ITEM_START_ROW = 28       # first base item
NUM_BASES = len(HQ_BASES)  # 9
ITEM_END_ROW = ITEM_START_ROW + NUM_BASES - 1  # 36
TOTAL_ROW = ITEM_END_ROW + 1   # 37
NOTES_HEADER_ROW = 39
NOTES_START_ROW = 40

# Cells that the refresh procedure (refresh-procedure.md §6) declares as
# *user-tuned* — preserved across refreshes via the duplicate-tab carry-over.
# These ranges are emitted by default for the very-first push (Sheet1, when no
# prior tab exists), but `build_updates(preserve_user_config=True)` skips them
# so the dated-tab flow doesn't blow away your tweaks.
#
# v1.6 changes (2026-05-12):
#   - B5 dropped from this set — it's now a FORMULA (trailing growth rate
#     derived from B7 + the weekly daily-avg table). The formula refresh on
#     every push is correct behaviour.
#   - B7 stays in the set but its MEANING changed: was "Event Week Start"
#     (date), now "Trailing Growth Window (weeks)" (integer). The migration
#     flag --reset-config B7 force-writes the new default on the first
#     v1.6 refresh; subsequent refreshes preserve user edits.
#   - B8 dropped: was "Event Bump", now "Initial Inventory Anchor Date"
#     (info-only, derived from B7 and the closing-report dates).
USER_TUNED_RANGES = {
    'B6',   # Target % of Total Initial Inventory
    'B7',   # Trailing Growth Window (weeks) — was Event Week Start in v1.5
    'B12',  # Min Units per Base (floor)
    *(f'G{r}' for r in range(ITEM_START_ROW, ITEM_END_ROW + 1)),  # Δ Adjust col
}


def build_updates(tab_name='Sheet1', preserve_user_config=None,
                  reset_config=None, growth_window_weeks=GROWTH_WINDOW_WEEKS_DEFAULT):
    """Generate the cell-update set for the AKSHAYA forecast sheet.

    Args:
      tab_name: target tab to write to. Default 'Sheet1' (legacy/first-run path).
        For history-preserving refreshes (the standard flow as of 2026-05-12),
        pass the snapshot date — e.g. tab_name='2026-05-12'.
      preserve_user_config: if True, omits the cells listed in USER_TUNED_RANGES
        (B6/B7/B12/G28:G36) so the user's tweaks from the prior tab carry
        through unchanged. Defaults to None → auto-detect: True iff tab_name is
        NOT 'Sheet1'.
      reset_config: optional set of cell ranges to FORCE-write even though
        they're in USER_TUNED_RANGES. Used for one-time migrations — e.g.
        v1.5 → v1.6 needs to overwrite the carried-over B7=event-date with the
        new B7=integer-weeks semantic. Example: `reset_config={'B7'}`.
      growth_window_weeks: default N for the trailing-window growth (used when
        we have to emit B7 fresh — i.e. on Sheet1 path or when B7 is in
        reset_config). Defaults to GROWTH_WINDOW_WEEKS_DEFAULT (3).

    Layout (v1.6, 2026-05-12):
      The item table is anchored at row 27 (header) / 28 (first item). Weekly
      section: rows 15..21, freshness notice at 22, forecast title at 23.
      Older weeks beyond the 7-row cap live on older dated tabs.

      CONFIG (rows 4-12):
        B5  Trailing Growth Rate (% per week)   ← FORMULA: derives from B7 + D14:D21
        B6  Target % of Total Initial Inventory ← USER-TUNED, default 95
        B7  Trailing Growth Window (weeks)      ← USER-TUNED, default 3
        B8  Initial Inventory Anchor Date       ← INFO (derived)
        B9  Inventory Snapshot Date             ← INFO (latest closing report date)
        B10 Current Daily Orders (info)         ← INFO (latest full-week avg)
        B11 First-Report Date (info)            ← INFO (earliest closing in data)
        B12 Min Units per Base (floor)          ← USER-TUNED, default 6
    """
    if preserve_user_config is None:
        preserve_user_config = (tab_name != 'Sheet1')
    if reset_config is None:
        reset_config = set()

    # Hard cap: how many weekly rows can fit between WEEKLY_DATA_START (15) and
    # WARNING_ROW (22, exclusive). 22 - 15 = 7 rows. Anything older gets
    # truncated; the dated tabs preserve full history across snapshots.
    MAX_WEEKLY_ROWS = WARNING_ROW - WEEKLY_DATA_START  # 7

    inventory = load_inventory_timeseries()
    dates = sorted(inventory.keys())
    latest_date = dates[-1]
    latest = inventory[latest_date]

    consumption = compute_per_item_consumption(inventory)
    daily_orders = load_square_orders()
    weekly = _weekly(daily_orders)

    full_weeks = [w for w in weekly.values() if w['days'] == 7]
    current_daily_avg = full_weeks[-1]['daily_avg'] if full_weeks else list(weekly.values())[-1]['daily_avg']

    updates = []
    def u(rng, val):
        if preserve_user_config and rng in USER_TUNED_RANGES and rng not in reset_config:
            return
        updates.append({"range": f"{tab_name}!{rng}", "value": str(val)})

    # v1.6: derive trailing growth + anchor date BEFORE writing CONFIG block.
    trailing_growth = compute_trailing_growth_rate(weekly, n_weeks=growth_window_weeks)
    anchor_date = compute_window_start_anchor_date(weekly, n_weeks=growth_window_weeks)
    anchored_inv, anchor_used_date = resolve_inventory_at_anchor(inventory, anchor_date)
    if not anchored_inv:
        anchor_used_date = None
        anchored_inv = {}

    # === TITLE ===
    u('A1', 'AKSHAYA — HQ Base Inventory Forecast')
    u('A2', f'Data through {latest_date}  |  All values below are formula-driven from CONFIG')

    # === CONFIG (rows 4-12, v1.6 layout) ===
    u('A4', 'CONFIG (edit B6/B7/B12; entire forecast recomputes)')
    for col in 'BCDEFGHI':
        u(f'{col}4', '')

    first_closing_date = dates[0]

    # B5 is a FORMULA — geometric-mean weekly growth over the last $B$7 weeks of
    # the displayed weekly table (D15:D21). Recomputes when the user edits B7.
    # Falls back to 0% if B7 ≤ 1 or B7 exceeds available weeks (IFERROR).
    b5_formula = (
        '=IF($B$7<=1, 0, IFERROR(ROUND('
        '(POWER(INDEX($D$15:$D$21, COUNT($D$15:$D$21)) / '
        'INDEX($D$15:$D$21, COUNT($D$15:$D$21) - $B$7 + 1), 1/($B$7-1)) - 1) * 100, 1), 0))'
    )

    # Each tuple: (A-col label, B-col value, C-col description).
    # Note: B5 is a formula; for B7/B8/B9/B10/B11 we compute and pass a value.
    config_rows = [
        ('Trailing Growth Rate (% per week)', b5_formula,
         '← Derived from B7 + the Weekly table (geometric mean). Edit B7 to retune.'),
        ('Total Tub Capacity (units)', 130,
         '← Absolute total tubs you can fit. v1.9 default bumped 120→130 when Blade was dropped (one fewer item to share capacity).'),
        ('Trailing Growth Window (weeks)', growth_window_weeks,
         '← How many recent full weeks to use for B5. Default 3. Integer ≥ 2.'),
        ('Initial Inventory Anchor Date', anchor_used_date or '—',
         '← Closing report used for B28:B36 — Sunday before the trend window starts.'),
        ('Inventory Snapshot Date', latest_date,
         '← Latest closing-report date; "as-of" for Current Stock (C col).'),
        ('Current Daily Orders (info)', round(current_daily_avg, 1),
         f'← Latest full-week avg (Square through {max(daily_orders.keys())})'),
        ('First-Report Date (info)', first_closing_date,
         '← Earliest ClickUp closing report in the data set (reference only).'),
        ('Min Order per Free Item', 5,
         '← Min order qty per item that\'s NOT stuck (v1.8). Stuck items (C > D×T_init) skip this floor; let them drain.'),
    ]
    for i, (label, val, note) in enumerate(config_rows):
        r = CONFIG_START + i
        u(f'A{r}', label)
        u(f'B{r}', val)
        u(f'C{r}', note)
        for col in 'DEFGHI':
            u(f'{col}{r}', '')

    # === WEEKLY ORDER VOLUME (rows 12-18) ===
    u(f'A{WEEKLY_HEADER_ROW}', 'WEEKLY ORDER VOLUME (from Square)')
    for col in 'BCDEFGHI':
        u(f'{col}{WEEKLY_HEADER_ROW}', '')

    weekly_headers = ['Week Starting', 'Days', 'Total Orders', 'Daily Avg', 'WoW Growth']
    for col_idx, h in enumerate(weekly_headers):
        u(f'{chr(65+col_idx)}{WEEKLY_COLS_ROW}', h)
    for col in 'FGHI':
        u(f'{col}{WEEKLY_COLS_ROW}', '')

    # Collect & truncate to the most recent MAX_WEEKLY_ROWS full weeks. WoW
    # growth is computed against the immediately preceding *displayed* week so
    # the first row may show "N/A" if older data was trimmed.
    sorted_full = [(k, v) for k, v in sorted(weekly.items()) if v['days'] == 7]
    if len(sorted_full) > MAX_WEEKLY_ROWS:
        sorted_full = sorted_full[-MAX_WEEKLY_ROWS:]

    row = WEEKLY_DATA_START
    prev_avg = None
    for week_key, info in sorted_full:
        avg = info['daily_avg']
        growth = ((avg / prev_avg) - 1) * 100 if prev_avg else None
        u(f'A{row}', week_key)
        u(f'B{row}', info['days'])
        u(f'C{row}', int(info['total']))
        u(f'D{row}', round(avg, 1))
        u(f'E{row}', f'{growth:+.1f}%' if growth is not None else 'N/A')
        for col in 'FGHI':
            u(f'{col}{row}', '')
        prev_avg = avg
        row += 1
    while row < WARNING_ROW:
        for col in 'ABCDEFGHI':
            u(f'{col}{row}', '')
        row += 1

    # === DATA FRESHNESS WARNING (row 20) ===
    last_full_week_end = full_weeks_sorted[-1] if (full_weeks_sorted := [k for k, v in sorted(weekly.items()) if v['days'] == 7]) else None
    if last_full_week_end:
        from datetime import datetime as _dt, timedelta as _td
        end_date = (_dt.strptime(last_full_week_end, '%Y-%m-%d') + _td(days=6)).strftime('%Y-%m-%d')
        u(f'A{WARNING_ROW}', f'Square data through {end_date} (last full week)  |  ClickUp closing reports through {latest_date}')
    else:
        u(f'A{WARNING_ROW}', f'Square data through {max(daily_orders.keys())}  |  ClickUp closing reports through {latest_date}')
    for col in 'BCDEFGHI':
        u(f'{col}{WARNING_ROW}', '')

    # Row between WARNING and FORECAST_TITLE — blank
    for col in 'ABCDEFGHI':
        u(f'{col}{WARNING_ROW + 1}', '')

    # === HQ BASE FORECAST (rows 23-37) ===
    u(f'A{FORECAST_TITLE_ROW}',
      'HQ BASE FORECAST — Equalize-DoS allocation (capacity = B6, target T = ($B$6 − stuck) / SUM(D_free), B12 = min order per free item)')
    for col in 'BCDEFGHI':
        u(f'{col}{FORECAST_TITLE_ROW}', '')

    # Item-range references used in formulas below.
    total_range_B = f'$B${ITEM_START_ROW}:$B${ITEM_END_ROW}'
    total_range_C = f'$C${ITEM_START_ROW}:$C${ITEM_END_ROW}'
    total_range_D = f'$D${ITEM_START_ROW}:$D${ITEM_END_ROW}'
    total_range_E = f'$E${ITEM_START_ROW}:$E${ITEM_END_ROW}'
    total_range_F = f'$F${ITEM_START_ROW}:$F${ITEM_END_ROW}'
    total_range_G = f'$G${ITEM_START_ROW}:$G${ITEM_END_ROW}'  # Δ column

    # Summary rows: STATIC TEXT computed in Python at push time, simulating the
    # equalize-DoS model with default config (B6=130, B12=5). User edits to
    # B6/B12/G in-sheet ARE picked up by the live formulas (helpers K28-K31 +
    # E-column), but the summary text below stays as a refresh-time snapshot.
    sum_initial = sum(round(anchored_inv.get(it, DAY1_REFERENCE_INVENTORY[it]), 2)
                       for it in HQ_BASES)
    capacity_default = 130  # B6 default; v1.9 bumped 120→130 when Blade was dropped
    b12_default = 5  # B12 default preserved (was 6 pre-v1.7; user kept 5)
    sum_D = sum(round(consumption.get(it, {}).get('rate', 0.0), 3) for it in HQ_BASES) or 0.001
    sum_C = sum(round(consumption.get(it, {}).get('current_stock', 0) or 0, 2)
                 for it in HQ_BASES)

    # --- Equalize-DoS simulation (matches the sheet formula) ---
    # Step 1: T_init from full SUM(D)
    t_init = capacity_default / sum_D
    # Step 2: classify stuck (C > D*T_init) vs free
    items_d = {it: round(consumption.get(it, {}).get('rate', 0.0), 3) for it in HQ_BASES}
    items_c = {it: round(consumption.get(it, {}).get('current_stock', 0) or 0, 2) for it in HQ_BASES}
    stuck = {it for it in HQ_BASES if items_c[it] > items_d[it] * t_init}
    free = [it for it in HQ_BASES if it not in stuck]
    sum_c_stuck = sum(items_c[it] for it in stuck)
    sum_d_free = sum(items_d[it] for it in free) or 0.001
    # Step 3: T_refined
    t_refined = (capacity_default - sum_c_stuck) / sum_d_free if sum_d_free > 0 else t_init

    sum_F = 0.0
    sum_E = 0
    item_dos = {}
    for it in HQ_BASES:
        d = items_d[it]
        c = items_c[it]
        if it in stuck:
            e_qty = max(0, round(0))  # no order on stuck items at default G=0
        else:
            equalize_target = d * t_refined
            e_raw = max(0, round(equalize_target - c))
            e_qty = max(b12_default, e_raw)  # B12 floor applies to free items only
        sum_E += e_qty
        f_val = round(c + e_qty, 2)
        sum_F += f_val
        item_dos[it] = (f_val / d) if d > 0 else 0

    # Capacity status
    capacity_pct = round(sum_F / capacity_default * 100, 0) if capacity_default else 0
    over_cap = sum_F > capacity_default
    cap_status = (
        f'⚠ OVER CAPACITY ({sum_F:.1f} > {capacity_default} by {sum_F - capacity_default:.1f} tubs)'
        if over_cap else
        f'{capacity_pct:.0f}% of capacity'
    )

    # In-band count: items whose DoS is within ±4 days of t_refined
    in_band = [it for it in HQ_BASES if abs(item_dos[it] - t_refined) <= 4]
    out_band = [it for it in HQ_BASES if it not in in_band]
    out_band_str = ', '.join(f'{it} ({item_dos[it]:.0f}d)' for it in out_band) or 'none'

    u(f'A{SUMMARY_ROW_1}',
      f'Tub Capacity (B6): {capacity_default} tubs  |  '
      f'Post-Order (F{TOTAL_ROW}): {sum_F:.1f} tubs ({cap_status})  |  '
      f'Current Total: {sum_C:.1f} tubs  |  '
      f'Anchor-Date Initial: {sum_initial:.1f} (info)')
    u(f'A{SUMMARY_ROW_2}',
      f'Equalize-DoS Target: {t_refined:.1f}d  |  '
      f'In-band (±4d): {len(in_band)} of {len(HQ_BASES)}  |  '
      f'Outliers: {out_band_str}  |  '
      f'Order Total: {sum_E}  |  '
      f'Trailing Growth: {trailing_growth["rate_pct"]:+.1f}%/wk')
    for col in 'BCDEFGHI':
        u(f'{col}{SUMMARY_ROW_1}', '')
        u(f'{col}{SUMMARY_ROW_2}', '')

    # Row between summary and item header — blank
    for col in 'ABCDEFGHI':
        u(f'{col}{SUMMARY_ROW_2 + 1}', '')

    # Item header row 27
    # NEW (v1.3): column G is user-editable Δ Adjust (±units). DoS moved to H.
    headers = ['Item', 'Initial Inventory', 'Current Stock', 'Avg Use/Day',
               'Order Qty', 'Post-Order Stock', 'Δ Adjust', 'Days of Supply']
    for col_idx, h in enumerate(headers):
        u(f'{chr(65+col_idx)}{ITEM_HEADER_ROW}', h)
    # Clear stale column I only (H now has header)
    u(f'I{ITEM_HEADER_ROW}', '')

    # Item data rows (27..35) — static inputs A,B,C + formulas D,E,F,G
    #
    # Current Stock (C column):
    #   - Default: raw value from the latest single closing report (trust latest).
    #   - Denoised only when raw_latest looks implausibly high vs recent median
    #     (>30% + 0.5 units above it) — inventory can't go up without a restock,
    #     so such a reading is almost certainly a typo. See forecast_v2's
    #     `compute_per_item_consumption` docstring for full rule.
    #   - The user can always override C{r} manually in the sheet; all formulas
    #     downstream (D, E, F, G) will recompute accordingly.
    for i, item in enumerate(HQ_BASES):
        r = ITEM_START_ROW + i
        # Initial Inventory (B col): NEW in v1.6 — stock at trend-window-start
        # anchor date (Sunday before the trailing-growth window begins). Falls
        # back to the hardcoded day-1 reference only if no closing report exists
        # at or before the anchor date.
        initial = anchored_inv.get(item, DAY1_REFERENCE_INVENTORY[item])
        cinfo = consumption.get(item, {})
        current = cinfo.get('current_stock')
        raw_fallback = latest.get(item, 0) or 0
        stock = round(current if current is not None else raw_fallback, 2)
        rate = round(cinfo.get('rate', 0.0), 3)

        u(f'A{r}', item)
        u(f'B{r}', round(initial, 2))
        u(f'C{r}', stock)

        # Avg Use/Day (D) — STATIC VALUE as of 2026-05-12 refactor.
        #
        # WAS: =(Initial − Current) / (Snapshot − Opening) … but ClickUp restocks
        # between 4/22-5/11 broke the monotone-decrease assumption that formula
        # required. The new method (sum of downward moves over last 14 days / 14)
        # needs the full timeseries, which isn't in the sheet — so the rate is
        # computed in `forecast_v2.compute_per_item_consumption` and written here.
        #
        # If you disagree with a rate, edit the cell directly — E, F, H will
        # recompute. To re-derive: re-run the refresh procedure.
        u(f'D{r}', rate)

        # Allocation logic (v1.8, 2026-05-12 PM — equalize-DoS with stuck-aware):
        #   Goal: maximize the number of items whose Days-of-Supply land within
        #   ±4 days of a shared target T_refined. Achieved by:
        #
        #   T_init    = $B$6 / SUM(D)                                ($K$28)
        #   stuck     = items where C > D × T_init  (already overstocked)
        #   T_refined = ($B$6 − SUM(C_stuck)) / SUM(D_free)          ($K$31)
        #   per-item target_F = IF stuck: C  (no order);
        #                       ELSE:     D × T_refined
        #   E = IF stuck:  MAX(0, ROUND(G))       (G can override, no B12 floor on stuck)
        #       ELSE:      MAX(B12, MAX(0, ROUND(target_F − C + G)))
        #
        # Rationale:
        #  - Free items cluster tightly at T_refined days of supply (low spread).
        #  - Stuck items stay at C (can't un-order); show up as DoS outliers
        #    until consumption drains them. They are flagged in summary row 2.
        #  - B12 floor applies ONLY to free items (forcing more onto a stuck
        #    item is perverse; it makes the overstock worse).
        #  - ROUND (not CEILING) so SUM(F) lands close to B6 rather than always
        #    overshooting. The user said "we can order less" — ROUND honors that.
        #  - Δ (col G) is added inside the round, so G is the manual override knob
        #    for both stuck and free items.
        is_stuck = f'$C{r} > $D{r} * $K$28'
        target_F_free = f'$D{r} * $K$31'  # equalize-DoS target
        u(f'E{r}', (
            f'=IF({is_stuck}, '
            f'MAX(0, ROUND($G{r}, 0)), '
            f'MAX($B$12, MAX(0, ROUND({target_F_free} - $C{r} + $G{r}, 0))))'
        ))
        u(f'F{r}', f'=ROUND(C{r}+E{r}, 2)')

        # G = Δ Adjust (user input, default 0). Positive = order more, negative = order less.
        u(f'G{r}', 0)

        # Days of Supply (column H): day-by-day simulation with weekly-compounding growth.
        # v1.6 (2026-05-12): event-bump term removed. The trailing-growth rate in
        # B5 already absorbs any sustained post-event lift.
        #   - Week aligned to Monday of snapshot date ($B$9)
        #   - Rate on day d: D × (1 + B5%)^week_index
        #   - Returns first day where cumulative consumption ≥ post-order stock
        dos_formula = (
            f'=IF(D{r}=0,"—",ARRAYFORMULA(LET('
            f'ws,$B$9-WEEKDAY($B$9,2)+1,'
            f'dsnap,$B$9-ws,'
            f'days,SEQUENCE(400,1,1,1),'
            f'dabs,dsnap+days,'
            f'wk,INT(dabs/7),'
            f'gm,POWER(1+$B$5/100,wk),'
            f'r,D{r}*gm,'
            f'cum,SCAN(0,r,LAMBDA(a,b,a+b)),'
            f'IFERROR(XMATCH(F{r},cum,1),400))))'
        )
        u(f'H{r}', dos_formula)

        # Clear I on this row
        u(f'I{r}', '')

    # Equalize-DoS helper cells (K28-K31) — referenced by every E-row formula.
    # Placed in column K, off the main visible table (A-I are the user-facing
    # columns). The label column J28 gives a hint if the user scrolls right.
    #
    #   K28 = T_init     = $B$6 / SUM(D)        — initial DoS target
    #   K29 = sum_C_stuck = sum of C where C > D × T_init
    #   K30 = sum_D_free  = sum of D where C ≤ D × T_init
    #   K31 = T_refined  = ($B$6 − sum_C_stuck) / sum_D_free   — equalize target
    #
    # These four cells make the live E-formula recompute correctly when the
    # user edits B6, B12, or any C value in the sheet. Without them, every
    # E-cell would need a 400-char SUMPRODUCT inline — too brittle for the
    # MCP's JSON-arg parser.
    u(f'J{ITEM_START_ROW}', 'T_init (days)')
    u(f'K{ITEM_START_ROW}', (
        f'=$B$6 / MAX(0.001, SUM({total_range_D}))'
    ))
    u(f'J{ITEM_START_ROW + 1}', 'SUM(C) of stuck items')
    u(f'K{ITEM_START_ROW + 1}', (
        f'=SUMPRODUCT(({total_range_C} > {total_range_D} * $K${ITEM_START_ROW}) '
        f'* {total_range_C})'
    ))
    u(f'J{ITEM_START_ROW + 2}', 'SUM(D) of free items')
    u(f'K{ITEM_START_ROW + 2}', (
        f'=SUMPRODUCT(({total_range_C} <= {total_range_D} * $K${ITEM_START_ROW}) '
        f'* {total_range_D})'
    ))
    u(f'J{ITEM_START_ROW + 3}', 'T_refined (equalize target, days)')
    u(f'K{ITEM_START_ROW + 3}', (
        f'=IF($K${ITEM_START_ROW + 2} > 0, '
        f'($B$6 - $K${ITEM_START_ROW + 1}) / $K${ITEM_START_ROW + 2}, '
        f'$K${ITEM_START_ROW})'
    ))

    # TOTAL row — F is the key cell: tracks ACTUAL post-order (sum of C+E).
    # In v1.8 (equalize-DoS) this should land within ±1-2 tubs of B6 because
    # ROUND (not CEILING) is used. SUM(F) > B6 when stuck items overflow the
    # cooler; the summary row 1 flags this with OVER CAPACITY.
    u(f'A{TOTAL_ROW}', 'TOTAL')
    u(f'B{TOTAL_ROW}', f'=ROUND(SUM({total_range_B}),1)')
    u(f'C{TOTAL_ROW}', f'=ROUND(SUM({total_range_C}),1)')
    u(f'D{TOTAL_ROW}', f'=ROUND(SUM({total_range_D}),2)')
    u(f'E{TOTAL_ROW}', f'=ROUND(SUM({total_range_E}),0)')
    u(f'F{TOTAL_ROW}', f'=ROUND(SUM({total_range_F}),1)')
    u(f'G{TOTAL_ROW}', f'=ROUND(SUM({total_range_G}),1)')  # Σ of user deltas
    u(f'H{TOTAL_ROW}', '')
    u(f'I{TOTAL_ROW}', '')

    # Blank rows between TOTAL and NOTES_HEADER_ROW. When NUM_BASES shrinks
    # (e.g. v1.9 dropped Blade, 9 → 8 items), the previous layout's TOTAL row
    # and downstream stale cells need to be explicitly cleared so the live
    # sheet doesn't show ghost data from the old layout. Iterates every row
    # between TOTAL_ROW+1 and NOTES_HEADER_ROW-1 across cols A-K (K covers the
    # equalize-DoS helpers so any stale K-row gets wiped too).
    for gap_r in range(TOTAL_ROW + 1, NOTES_HEADER_ROW):
        for col in 'ABCDEFGHIJK':
            u(f'{col}{gap_r}', '')

    # NOTES (rows 38-44)
    u(f'A{NOTES_HEADER_ROW}', 'NOTES')
    for col in 'BCDEFGHI':
        u(f'{col}{NOTES_HEADER_ROW}', '')

    noisy_items = [item for item in HQ_BASES if consumption.get(item, {}).get('noisy')]
    restock_items = [
        (item, consumption[item].get('restocks_detected') or [])
        for item in HQ_BASES
        if consumption.get(item, {}).get('restocks_detected')
    ]
    if restock_items:
        rs_summary = []
        for item, rs in restock_items:
            biggest = max(rs, key=lambda r: r['delta'])
            rs_summary.append(
                f"{item} ({len(rs)} restock(s), biggest +{biggest['delta']:.1f} "
                f"on {biggest['date_to']})"
            )
        restock_note = (
            '• 🚚 RESTOCKS detected in the 14-day rate window: '
            + '; '.join(rs_summary)
            + '. Each upward jump is excluded from the consumption sum, so the '
              'rate reflects real usage, not net stock change.'
        )
    else:
        restock_note = '• No restocks detected in the 14-day rate window.'

    growth_note = (
        f'• Trailing growth (B5): {trailing_growth["rate_pct"]:+.1f}%/week, '
        f'geometric mean over the last {trailing_growth["n_weeks_used"]} full weeks '
        f'({trailing_growth.get("window_start_week", "?")} → {trailing_growth.get("window_end_week", "?")}). '
        'Edit B7 to retune (e.g. 2 to react faster, 4 for more smoothing). '
        + (f'⚠ FALLBACK: {trailing_growth["fallback_reason"]}.' if trailing_growth.get('fallback_reason') else '')
    )
    anchor_note = (
        '• Initial Inventory (B col) = per-item stock as of the Sunday-closing '
        f'BEFORE the trailing window starts. Current anchor date: '
        f'{anchor_used_date or "— (no report; fell back to day-1 reference)"}. '
        'INFORMATIONAL only in v1.7+: B no longer drives the target — that comes '
        'from B6 (absolute capacity). B stays as a useful "where were we 3 weeks ago" sanity check.'
    )

    notes = [
        f'• Data window: closing reports {dates[0]} → {latest_date} ({len(dates)} days). '
        f'Rate window = last 14 days (restock-aware).',
        growth_note,
        anchor_note,
        '• Current Stock (C) = raw latest closing report. No more auto-denoising — restocks make the "monotone-decrease" invariant invalid, so we can\'t reliably tell typos from real upward moves. Manually override any C-cell if you spot a clear data-entry error.',
        '• Avg Use/Day (D) = STATIC VALUE computed in Python from sum-of-downward-moves over the last 14 days / 14. NOT a formula (would need the full timeseries in-sheet). Editing C does NOT recompute D. To re-derive, re-run the refresh procedure. You can override any D-cell manually; E, F, H will recompute.',
        '• ALLOCATION (v1.8, equalize-DoS): T_init = B6/SUM(D) → stuck = items where C > D×T_init → T_refined = (B6 − SUM(C_stuck)) / SUM(D_free) (cell K31) → for STUCK items E = MAX(0, ROUND(G)) (no B12 floor; let them drain); for FREE items E = MAX(B12, MAX(0, ROUND(D×T_refined − C + G))) → F = C + E. Goal: maximize items within ±4d of T_refined. Summary row 2 lists outliers.',
        '• B12 = minimum units per base (default 6), hard floor applied to every item unconditionally WHEN Δ=0. CEILING on the order qty ensures the floor is never clipped by rounding. Edit B12 to change the safety floor.',
        '• Δ Adjust (G col) = manual override per item, default 0. Type +2 to order 2 more units; type -3 to order 3 fewer. Δ is applied AFTER the floor, so a negative Δ intentionally can take F below B12 (explicit user decision).',
        '• User-tuneable CONFIG cells (preserved across refreshes): B6 (Tub Capacity, abs), B7 (Window weeks), B12 (Floor), G28:G36 (per-item Δ). B5/B8/B9/B10/B11 are refreshed every push.',
        '• Days of Supply (H col): weekly-compounding growth from B5; event-bump term removed in v1.6 since the trailing-window rate absorbs sustained shifts naturally.',
        '• ⚠ Low DoS items indicate per-item rate is high relative to post-order stock — consider raising B6, B12, or the per-item Δ.',
        restock_note,
        (f'• ⚠ NOISY items (rate ≈ 0 — no consumption detected in 14d window): {", ".join(noisy_items)}. '
         'Likely fully-restocked-and-idle, untracked in closing reports, or data gap.')
        if noisy_items else '• No items flagged noisy (every base showed downward consumption in the window).',
        '• Phase 2 backlog: Square recipes for per-item demand, Square API for auto-refresh, ClickUp Chat MCP for closing reports.',
    ]
    for i, note in enumerate(notes):
        r = NOTES_START_ROW + i
        u(f'A{r}', note)
        for col in 'BCDEFGHI':
            u(f'{col}{r}', '')

    # Clear stale rows beyond the notes (in case the prior layout had more rows).
    for r in range(NOTES_START_ROW + len(notes), 55):
        for col in 'ABCDEFGHI':
            u(f'{col}{r}', '')

    # Clear stale column K (test cell)
    u('K1', '')

    return updates


if __name__ == '__main__':
    import sys
    # CLI flags:
    #   --tab=<name>             target tab to write to (default: latest closing date)
    #   --window-weeks=<int>     trailing-growth window size (default: 3)
    #   --reset-config=B7,B8     comma-separated cells to FORCE-write even if in
    #                            USER_TUNED_RANGES. Used for one-time migrations.
    tab_arg = None
    window_weeks_arg = GROWTH_WINDOW_WEEKS_DEFAULT
    reset_cfg_arg = set()
    for a in sys.argv[1:]:
        if a.startswith('--tab='):
            tab_arg = a.split('=', 1)[1]
        elif a.startswith('--window-weeks='):
            window_weeks_arg = int(a.split('=', 1)[1])
        elif a.startswith('--reset-config='):
            reset_cfg_arg = {c.strip() for c in a.split('=', 1)[1].split(',') if c.strip()}
    if tab_arg is None:
        inv = load_inventory_timeseries()
        tab_arg = sorted(inv.keys())[-1]

    updates = build_updates(tab_name=tab_arg, growth_window_weeks=window_weeks_arg,
                            reset_config=reset_cfg_arg)
    out = Path(__file__).parent / 'sheet-updates-v3.json'
    with open(out, 'w') as f:
        json.dump(updates, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(updates)} updates to {out} (target tab: '{tab_arg}', "
          f"growth_window={window_weeks_arg}, reset_config={sorted(reset_cfg_arg) or 'none'})")
    max_row = max(int(''.join(x for x in up['range'].split('!')[1] if x.isdigit())) for up in updates)
    print(f"Max row: {max_row}")
    print("\nFirst 10 updates:")
    for up in updates[:10]:
        print(f"  {up['range']}: {up['value'][:80]}")
    print(f"\nNEXT: agent should ensure tab '{tab_arg}' exists (via gsheets_duplicate_tab "
          f"from the prior canonical tab), then push these updates via gsheets_batch_update.")
