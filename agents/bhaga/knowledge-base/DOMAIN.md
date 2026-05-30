# BHAGA — Product & Domain Reference (data dictionary)

This is the **domain glossary** for BHAGA: what the business concepts mean (orders, items, labor,
hourly vs full-time, tips, KDS, reviews, the various metrics) and exactly which sheet column each one
maps to. It exists so a future agent can understand the *meaning* of the data before changing the
*code*.

- **Where the data lives / how it's computed:** [`agents/bhaga/scripts/README.md`](../scripts/README.md)
  (pipeline + "Extending the model").
- **Behavioral invariants:** [`../../../.cursor/rules/bhaga.md`](../../../.cursor/rules/bhaga.md).
- **Operate the live system:** [`../../../RUNBOOK.md`](../../../RUNBOOK.md).
- **Exact headers / natural keys (the contract):** `skills/tip_ledger_writer/schema.py` (raw + some
  model tabs) and the `build_*_rows` functions in `agents/bhaga/scripts/update_model_sheet.py`
  (the labor / tip-alloc / forecast / review tabs). **When columns change, update this file too**
  (see `.cursor/rules/doc-maintenance.md`).

---

## 1. The business in one paragraph

BHAGA serves a single coffee/food shop (Palmetto, Austin TX; Houston later). The shop sells through
**Square** (POS + KDS), pays staff through **ADP RUN** (time clock + payroll), and collects **Google
reviews** funneled into **ClickUp**. Every night BHAGA pulls these three sources, and produces: (a) a
**fair tip allocation** (each day's card-tip pool split by hours worked that day), (b) a **labor
model** (labor cost vs sales, throughput, saturation), (c) a **forecast / staffing plan**, and (d)
**review bonuses**. Money is handed to payroll; BHAGA never writes back to ADP automatically.

### Two workforce buckets (this is the "full-time vs part-time" the metrics refer to)

| Bucket | Who | In tip pool? | Counts toward… |
|---|---|---|---|
| **hourly** (a.k.a. tipped / part-time staff) | baristas / line staff paid hourly | **Yes** | throughput, saturation, the tip pool, `hourly_*` labor metrics |
| **fulltime** (a.k.a. manager / salaried / excluded) | the store manager + any salaried hire | **No** | `fulltime_*` labor metrics only; excluded from tip pool + throughput |

An employee lands in the **fulltime** bucket if **any** of: listed in config `excluded_from_tip_pool`,
`wage_rates.is_salaried == True`, or `wage_rates.excluded_from_labor_pct == True`. All three resolve
to the manager today; the union keeps it future-proof. "fulltime"/"hourly" here are **labor-model
buckets**, not ADP employment classes — there are no salaried employees in the account yet
(everyone is hourly; the manager is excluded by *name/policy*, not by a salaried flag).

---

## 2. Data sources & their quirks (must-know gotchas)

| Source | Pulled via | Quirk you must respect |
|---|---|---|
| **Square** transactions | `skills/square_tips/` (CSV export) | **Timestamps are in the Square account's display TZ (Eastern), not the shop's (Central).** An 11:30 PM CT sale shows as 12:30 AM ET next day. `transactions_backend.parse_csv()` converts to `America/Chicago`. |
| **Square** KDS / items | same export | KDS = Kitchen Display System; per-item prep timing. |
| **ADP RUN** timecards + earnings | `skills/adp_run_automation/` (Playwright) | **Times are already shop-local — no conversion.** **Open shifts (no clock-out) are silently omitted** from the export → always scrape after close. **Name formats differ between reports** ("LastName FirstName" in timecards vs "LastName, FirstName" in earnings) → `employee_aliases` normalizes to one canonical name or employees double-count. |
| **Google reviews** | ClickUp (`CLICKUP_PAT`) → `process_reviews.py` | Reviews are markdown messages prefixed `### Google Review`; parsed for post time, rating, reviewer, comment, and any named staff. |

All dates/timestamps in reports are **Central time** (`America/Chicago`). A day is "complete" only
after the shop closes; the nightly fires 21:30 CT.

---

## 3. The three workbooks & their tabs

Sheet IDs come from `store-profiles/palmetto.json` → `google_sheets`. Raw tabs upsert by natural key
(idempotent); model tabs are recomputed each run.

### A. `BHAGA ADP Raw` — labor source of truth

| Tab | Grain | Key columns |
|---|---|---|
| `shifts` | one row per (employee, **day**) | `regular_hours`, `ot_hours`, `doubletime_hours`, `total_hours`, `in_time`/`out_time`, `pay_period` |
| `punches` | one row per **clock punch** (split shifts → multiple rows) | `punch_idx_in_day`, `in_time`/`out_time`, per-punch hours |
| `wage_rates` | one row per **employee** | `wage_rate_dollars` (latest Regular rate), `ot_rate_dollars`, `is_salaried`, `excluded_from_labor_pct`, `rate_history_json` (audit trail) |

### B. `BHAGA Square Raw` — sales & operations source of truth

| Tab | Grain | Key columns |
|---|---|---|
| `transactions` | one row per **Square transaction** | `gross_sales_cents`, `discount_cents`, `tip_cents`, `net_total_cents`, `total_collected_cents`, `event_type` (`Payment` vs refund), local + source timestamps |
| `daily_rollup` | one row per **shop-local day** | `txn_count`, `gross_sales_cents`, `tip_cents`, `net_sales_cents`, `refund_cents` |
| `item_daily_rollup` | one row per **day** | `items_sold` (count of item line items), `units_sold` (sum of qty), `avg_item_price_cents` |
| `kds_daily` | one row per **day** | `completed_tickets`, `completed_items`, `median/p90/p95/p99_time_per_item_sec`, `pct_tickets_late`, `late_tickets`, `due_tickets` |

### C. `BHAGA Model` — derived, human-facing

| Tab | Grain | What it answers |
|---|---|---|
| `config` | key/value | Tunables the operator edits in-sheet (sheet IDs, store TZ, exclusions, bonus amounts, forecast targets, saturation threshold) |
| `daily` | per day | Quick daily ledger: hours, labor cost, sales, labor %, tips, tips/hour, ticket count, avg ticket |
| `labor_daily` | per day | The deep labor model (see §4) — hourly vs fulltime cost, labor % variants, throughput, saturation |
| `labor_weekly` | per ISO week | Weekly rollup of the labor model |
| `labor_period` | per pay period | Pay-period rollup of the labor model |
| `tip_alloc_daily` | per (day, employee) | Pool-by-day fair-share: hours, % of day's team hours, day pool, that day's allocation |
| `tip_alloc_period` | per (period, employee) | Period totals + reconciliation: `our_calc` vs `adp_paid`, `diff`, `likely_reason` |
| `period_summary` | per pay period | Period headline: team hours, tip pool, our total vs ADP total, employees with >$1 diff |
| `dow_hour` | per (day-of-week, hour) | Trailing-28-day heatmap of txns/sales/tips by weekday × hour |
| `review_bonus_period` | per (period, employee) | Review bonuses earned (see §6) |
| `labor_daily_forecast` | per future day | Live staffing planner (see §7) |

---

## 4. Metric glossary (the labor model — `labor_daily` / `labor_weekly` / `labor_period`)

These columns mirror Square's own terminology so they cross-reference the Square dashboard without
translation.

**Sales side**
- **`gross_sales`** — Square "Gross Sales": pre-discount item revenue.
- **`discounts`** — Square "Discounts" (stored negative).
- **`net_sales`** — `gross_sales + discounts`: post-discount, **ex-tax, ex-tips, ex-service-charge**.
  This is the **industry-standard labor% denominator** (tips are a customer→staff pass-through).
- **`tip_pool`** — Square "Tip" (kept separate from sales).
- **`net_sales_plus_tips`** — `net_sales + tip_pool`: total customer revenue ex-tax/ex-SC. Powers the
  "what share of every dollar walking in goes to labor" view.
- **`orders`** — count of **completed** Square transactions (`event_type == "Payment"`; refunds
  excluded, matching `net_sales`). This is BHAGA's unit of throughput.

**Labor side** (each metric exists for both buckets + a total)
- **`hourly_hours` / `hourly_labor_cost`** — tipped-staff hours and cost.
- **`fulltime_hours` / `fulltime_labor_cost`** — manager/salaried hours and cost.
- **`total_labor_cost`** — `hourly + fulltime`.
- Per-shift cost = `regular_hours × rate + ot_hours × (ot_rate or rate×1.5) + doubletime_hours ×
  rate × 2`. Employees missing a wage row (new hires) fall back to the **median hourly rate**.

**Labor % (two denominators × three scopes)**
- `hourly_pct_of_net_sales`, `hourly_pct_of_net_sales_plus_tips`
- `fulltime_pct_of_net_sales`, `fulltime_pct_of_net_sales_plus_tips`
- `total_labor_pct_of_net_sales`, `total_labor_pct_of_net_sales_plus_tips`
- `tips_pct_of_net_sales` — tip pool as a share of net sales.
- `all_in_cost_pct_of_net_sales_plus_tips` — labor + tips vs total revenue.

**Throughput / saturation** (denominator is **hourly** labor only — managers don't add bar throughput)
- **`*_labor_per_order`** — $ labor per order, by bucket and total.
- **`*_hours_per_order`**, **`*_hours_per_item`** — labor hours per order / per item.
- **`orders_per_labor_hour`** — `orders ÷ hourly_hours`. The core efficiency number.
- **`peak_hour_orders_per_labor_hour`** — worst single clock-hour's ratio (each shift is spread across
  the hours it covered). Catches an 11am–1pm rush hidden inside a calm daily average — the actionable
  "add a shift at peak" signal.
- **`over_saturation`** — `"OVER"` when `orders_per_labor_hour` exceeds config
  `saturation_orders_per_labor_hour`, else `"ok"` (operator color-codes it red). Blank when no
  hourly labor / no orders (instead of divide-by-zero).

**KDS (kitchen throughput / speed-of-service)** — from `kds_daily`, pooled for weekly/period
- **ticket** = a KDS order; **item** = a line within it. **`completed_tickets` / `completed_items`**.
- **`median / p90 / p95 / p99 time_per_item_sec`** — per-item prep-time distribution. No upper cap;
  the full tail is surfaced (a 15s lower floor filters tickets cleared without real prep).
- **`pct_tickets_late`**, **`late_tickets`**, **`due_tickets`** — SLA adherence.
- **`kds_pct_items_over_goal`** — share of items slower than the goal (config
  `forecast_target_completion_time_per_item_sec`, default 420s = 7 min).

---

## 5. Tip allocation (pool-by-day fairness) — `tip_alloc_daily` / `tip_alloc_period`

Policy (the non-negotiable invariant): for **each day**,
`employee_share = (employee_hours_that_day ÷ total_eligible_team_hours_that_day) × that_day's_tip_pool`,
then summed across the period. Never pool the whole period's tips against the whole period's hours —
that underpays people who worked the high-tip days. The manager (tip-pool-excluded) is left out of
the denominator.

- `tip_alloc_daily`: `hours_worked`, `team_hours_eligible`, `pct_of_day_hours`, `day_pool`,
  `our_share`.
- `tip_alloc_period`: per-employee period totals with **reconciliation** against payroll:
  - **`our_calc`** — BHAGA's computed allocation.
  - **`adp_paid`** — what ADP actually paid (once earnings data lands).
  - **`diff` / `diff_pct`** — `our_calc − adp_paid`.
  - **`likely_reason`** — heuristic explanation when they diverge (open period, partial coverage, etc.).
  - **`coverage`** — how complete the period's source data is; **`is_open`** — period not yet closed/paid.

---

## 6. Review bonuses — `review_bonus_period`

Google reviews (5★ praise) earn baristas a bonus. Two modes (`allocate_bonus`):

- **Shoutout mode** (the review names specific staff): **only the named people** earn
  `review_named_bonus_dollars` (default $20) each. A shoutout **overrides exclusions** — even the
  tip-pool-excluded manager earns it if named.
- **Base mode** (generic 5★, no names): **every non-excluded shift member** earns
  `review_base_bonus_dollars` (default $10). Permanent + training exclusions apply here.

Per-review detail (raw review tab) carries `rating`, `reviewer`, `comment`, `named_baristas`,
`shift_date_credited`, `shift_members`, `total_bonus`, etc. `review_bonus_period` rolls it up per
(period, employee): `reviews_credited`, `named_count`, `base_dollars`, `named_dollars`,
`total_bonus`. Tunables (`review_base_bonus_dollars`, `review_named_bonus_dollars`,
`review_bonus_started_date`) live in the Model `config` tab. The rebuild is idempotent and runs every
night.

---

## 7. Forecast & staffing — `labor_daily_forecast`

A **live, in-sheet planning tool**: a trailing window of frozen past days + future rows where
**derived columns are Google Sheets formulas**, so editing an input recalculates the staffing
recommendation in the sheet. Inputs (editable per row): `orders`, `fulltime_hours`,
`target_labor_pct`, `target_hourly_labor_pct`, `target_time_per_item_sec`, `forecast_exclude`.

- **`recommended_hourly_hours`** — staffing solver output: hours needed for coverage/efficiency at the
  target item-completion time. Budget is a **check, not a cap** (never understaffs below coverage).
- **`budget_hours`** — hourly hours the labor budget allows after the manager's full-time cost.
- **`actual_labor_pct`** — `total_labor_cost ÷ net_sales` (**all** labor incl. manager).
- **`hourly_labor_pct`** — `hourly_cost ÷ net_sales` (**part-time only**, excludes manager).
- **`staffing_flag`** — `OVER_BUDGET` / `UNDER_BUDGET` / `OK` (keyed to the total-labor budget).
- **`hourly_staffing_flag`** — same idea keyed to `target_hourly_labor_pct`.
- **Accuracy columns** (Python-backfilled once a forecast day has a realized actual):
  `*_error_pct` per metric and **`forecast_mape`** (mean absolute % error) — so forecast quality is
  measurable over time.

Forecast targets default from config: `forecast_target_labor_pct` (0.25), `forecast_target_hourly_labor_pct`
(0.20), `forecast_fulltime_weekly_hours` (40), `forecast_target_completion_time_per_item_sec` (420).

---

## 8. Where to change things

- **Add a column / new tab:** `agents/bhaga/scripts/README.md` § Extending the model (Recipe A / B).
- **Tune a number (bonus amounts, targets, saturation threshold, exclusions):** the Model `config`
  tab — no code change.
- **Add a new metric definition here:** keep this glossary in lock-step with the schema and the
  `build_*_rows` functions whenever columns change.
