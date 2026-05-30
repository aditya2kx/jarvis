# skills/bhaga_labor

Labor-model helpers shared across BHAGA: workforce-bucket classification and
point-in-time "who was clocked in" headcounts from ADP punches. Pure functions —
no network, no IO, no clock reads — so they're fast and unit-testable.

## Why this exists

The two labor concepts BHAGA reuses everywhere live here so they don't drift:

1. **Workforce buckets** (`hourly` vs `fulltime`) — the same split used by
   `build_labor_daily_rows` in `update_model_sheet.py` and documented in
   `agents/bhaga/knowledge-base/DOMAIN.md` §1.
2. **Punch overlap** — "was employee X clocked in at time T", the same idea as
   `process_reviews.find_shift_for_post()`.

## Public API (`from skills.bhaga_labor import ...`)

| Function | Purpose |
|---|---|
| `classify_employee_bucket(employee_name, wage_rates_by_name, excluded_from_tip_pool)` | Returns `"hourly"` or `"fulltime"`. Fulltime if the employee is in `excluded_from_tip_pool`, or `is_salaried`, or `excluded_from_labor_pct` — identical to `build_labor_daily_rows`. |
| `index_punches_by_date(punches)` | Groups raw `punches` rows by shop-local `date` (build once, reuse per item line — avoids re-scanning all punches per row). |
| `count_staff_punched_in_at(*, item_sold_at_local, punches, wage_rates, excluded_from_tip_pool, punches_by_date=None)` | Distinct headcount clocked in at the item's sale time. Returns `staff_punched_in_{hourly,fulltime,total}_count`. Pass a prebuilt `punches_by_date` for speed. |

## Semantics & conventions

- **"Punched in at T"** = on `date_local`, a punch with `in_time <= T <= out_time`
  (shop-local `HH:MM[:SS]`; both ADP `HH:MM` and item `HH:MM:SS` are normalized).
- **Distinct headcount**, not labor-hours — each employee counts once per moment.
  This is point-in-time staffing, distinct from `labor_daily`'s hours/cost or
  peak-hour spreading. See DOMAIN.md §4.x.
- **`hourly` / `fulltime` are labor-model buckets**, not ADP employment classes.
- **Source = `punches`** (split-shift granularity), not day-aggregated `shifts`.

## Used by

- `agents/bhaga/scripts/item_operations.py` — adds the `staff_punched_in_*`
  columns to the Model `item_operations` tab.

## Performance

Designed for per-item-line use across a full day: build `punches_by_date` once,
then each line is `O(#punches that day)`. ~100–300 lines/day × ~15 staff is
milliseconds — the real cost is Sheets IO, not this math.

## Extending

If you add another consumer of punch overlap (e.g. fold
`process_reviews.find_shift_for_post()` in here), keep the bucket rules in
`classify_employee_bucket` as the single source of truth and update DOMAIN.md §1
in the same change.
