# BHAGA — Product & Domain Reference (data dictionary)

This is the **domain glossary** for BHAGA: what the business concepts mean (orders, items, labor,
hourly vs full-time, tips, KDS, reviews, the various metrics) and exactly which sheet column each one
maps to. It exists so a future agent can understand the *meaning* of the data before changing the
*code*.

- **Where the data lives / how it's computed:** [`agents/bhaga/scripts/README.md`](../scripts/README.md)
  (pipeline + "Extending the model").
- **Behavioral invariants:** [`../../../.cursor/rules/bhaga.mdc`](../../../.cursor/rules/bhaga.mdc).
- **Operate the live system:** [`../../../RUNBOOK.md`](../../../RUNBOOK.md).
- **Exact headers / natural keys (the contract):** `skills/tip_ledger_writer/schema.py` (raw + some
  model tabs) and the `build_*_rows` functions in `agents/bhaga/scripts/update_model_sheet.py`
  (the labor / tip-alloc / forecast / review tabs). **When columns change, update this file too**
  (see `.cursor/rules/doc-maintenance.mdc`).

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
| **Square** transactions | `skills/square_api/` (REST API, OAuth 2.0) | Square returns UTC timestamps; `export.py` converts to `America/Chicago`. Register orders use `closed_at`; Kiosk/3rd-party use `created_at`. Gift-card purchases excluded from gross sales. Split-tender orders are aggregated to one row per `order_id`. |
| **Square** KDS / items | `skills/square_api/kds_reporting.py` (Reporting API / Cube.js KDS cube) | `display_on_kds_at` is used as "Time Created" for completion-time math. Naive UTC timestamps from the Reporting API are explicitly treated as UTC before shop-local conversion. `time_due` enables late-ticket stats. |
| **ADP RUN** timecards + earnings | `skills/adp_run_automation/` (Playwright) | **Times are already shop-local — no conversion.** **Open shifts (no clock-out) are silently omitted** from the export → always scrape after close. **Name formats differ between reports** ("LastName FirstName" in timecards vs "LastName, FirstName" in earnings) → `employee_aliases` normalizes to one canonical name or employees double-count. |
| **Google reviews** | ClickUp (`CLICKUP_PAT`) → `process_reviews.py` | Reviews are markdown messages prefixed `### Google Review`; parsed for post time, rating, reviewer, comment, and any named staff. |

All dates/timestamps in reports are **Central time** (`America/Chicago`). A day is "complete" only
after the shop closes; the nightly fires 21:30 CT.

---

## 3. The four workbooks & every field

Sheet IDs come from `store-profiles/palmetto.json` → `google_sheets` (keys `bhaga_adp_raw`,
`bhaga_square_raw`, `bhaga_review_raw`, `bhaga_model`). **Raw** tabs upsert by natural key (idempotent). Most **model** tabs are recomputed each run
(clear-and-write); **`item_operations`** is upserted incrementally by natural key. The contract
(headers + natural keys) lives in
`skills/tip_ledger_writer/schema.py` for ADP/Square raw + some model tabs, in
`agents/bhaga/scripts/process_reviews.py` (`*_HEADER_ROW`) for review raw, and in the `build_*_rows`
functions of `update_model_sheet.py` / `forecast.py` for the labor / tip-alloc / forecast tabs.

> Convention: `*_cents` = integer cents (money), `*_dollars` = dollars-and-cents, `*_utc` /
> `*_iso` = timestamps, `*_local` / `*_ct` = shop-local (Central). `scraped_at_utc` /
> `ingested_at_utc` stamp when the row was last written.

### A. `bhaga_adp_raw` — labor source of truth (from ADP RUN)

**`shifts`** — one row per (employee, **day**). Key: `(date, employee_id)`. Source:
`skills/adp_run_automation/shift_backend.daily_shifts`.

| Field | Meaning |
|---|---|
| `date` | shop-local calendar day |
| `employee_id` | ADP file # (stable id) |
| `employee_name` | canonical name (after alias normalization) |
| `raw_employee_name` | exact spelling ADP returned (pre-normalization) |
| `in_time` / `out_time` | first clock-in / last clock-out (HH:MM, shop-local) |
| `regular_hours` | non-OT hours |
| `ot_hours` | overtime hours (>40/wk) |
| `doubletime_hours` | double-time hours (rare) |
| `total_hours` | `regular + ot + doubletime` |
| `punch_count` | number of clock punches that day (split shifts > 1) |
| `pay_period` | the pay period this day belongs to |

**`punches`** — one row per **clock punch** (split shifts emit multiple). Key:
`(date, employee_id, punch_idx_in_day)`. Source: `shift_backend.raw_punches`. Same columns as
`shifts` minus totals, plus `punch_idx_in_day` (0-based punch order within the day).

**`wage_rates`** — one row per **employee**. Key: `(employee_id,)`. Source:
`compensation_backend.compensation` (from the "Earnings and Hours V1" ADP report).

| Field | Meaning |
|---|---|
| `wage_rate_dollars` | most recent Regular hourly rate |
| `ot_rate_dollars` | overtime rate (usually 1.5×) |
| `is_salaried` | salaried flag (→ fulltime bucket). Nobody today. |
| `multi_rate` | employee has >1 active rate |
| `excluded_from_labor_pct` | explicit fulltime-bucket override from store profile (the manager) |
| `rate_history_json` | full rate-change audit trail |
| `raw_employee_names_json` | every ADP spelling seen for this person |

### B. `bhaga_square_raw` — sales & operations source of truth (from Square)

**`transactions`** — one row per **Square transaction**. Key: `(transaction_id,)`. Source:
`skills/square_api/ingest.py` → `export._build_transaction_rows` → `transactions_backend.parse_transaction_rows` (in-memory rows; no CSV file).

| Field | Meaning |
|---|---|
| `transaction_id` | Square's id |
| `event_type` | `Payment` (counts as an order) vs refund types (excluded from net) |
| `created_at_src_iso` | timestamp in the **Square account TZ (Eastern)** |
| `created_at_local_iso` | timestamp converted to **shop-local (Central)** |
| `date_local` / `hour_local` / `dow_local` | shop-local day / hour-of-day / weekday |
| `gross_sales_cents` | pre-discount item revenue |
| `discount_cents` | discounts (negative) |
| `tip_cents` | tip on this txn |
| `net_total_cents` | net for the txn |
| `total_collected_cents` | total charged incl. tax/tips |
| `source` | Square source channel (register, online, etc.) |
| `staff_name` | Square-attributed staff (NOT used for tip allocation — hours drive that) |
| `location` | Square location |
| `raw_date_csv` / `raw_time_csv` / `raw_tz_csv` | untouched CSV values, kept for audit |

**`daily_rollup`** — one row per **shop-local day**. Key: `(date_local,)`. Derived from
`transactions`. Fields: `txn_count`, `gross_sales_cents`, `tip_cents`, `net_sales_cents`,
`refund_cents`.

**`item_daily_rollup`** — one row per **day**. Key: `(date_local,)`. Source:
`transactions_backend.aggregate_daily_item_stats`. Fields: `items_sold` (count of item line items),
`units_sold` (sum of quantity), `gross_sales_cents`, `avg_item_price_cents` (= gross / items_sold).

**`item_lines`** — one row per **Square item sales line**. Key:
`(transaction_id, item_name, item_sold_at_local, line_seq)`. Source:
`transactions_backend.parse_item_sales_csv` (Item Sales Detail CSV). Fields include
`date_local`, `item_sold_at_local` (shop-local `YYYY-MM-DDTHH:MM:SS`), `item_name`, `category`,
`qty_sold`, money in cents, `event_type` (Payment and Refund lines are both kept), `transaction_id`,
`payment_id`, `location`, `channel`, `line_seq` (0-based row index in the source CSV).
Square's per-line `employee` field is **not** stored — staffing uses ADP punches instead.

**`kds_daily`** — one row per **day**, kitchen efficiency. Key: `(date_local,)`. Source:
`transactions_backend.aggregate_daily_kds_stats`.

| Field | Meaning |
|---|---|
| `completed_tickets` / `completed_items` | KDS tickets / line items finished |
| `median_time_per_item_sec` | median per-item prep time |
| `p90` / `p95` / `p99_time_per_item_sec` | tail of the per-item prep-time distribution (no upper cap) |
| `pct_tickets_late` | share of tickets past their due time |
| `shift_start` / `shift_end` | KDS active window |
| `late_tickets` / `due_tickets` | late count / total tickets with a due time |
| `per_item_times_json` | item-weighted per-item-seconds list (pooled for EXACT weekly/period percentiles) |

### C. `bhaga_review_raw` — Google reviews source of truth (from ClickUp)

Reviews arrive as ClickUp messages prefixed `### Google Review`. **No `config` tab** here. Built by
`process_reviews.py`.

**`reviews`** — one row per parsed review. Key: `review_id` (hash of post-time + reviewer +
comment-prefix).

| Field | Meaning |
|---|---|
| `review_id` | stable dedupe id |
| `post_ts_ct` / `post_date_ct` | when the review was posted (Central) |
| `rating` | star rating (1–5) |
| `reviewer` | reviewer name as posted |
| `comment` | review text |
| `named_baristas` | staff explicitly named in the review (`; `-separated) |
| `named_status` | how names were resolved (matched / unmatched / none) |
| `shift_date_credited` | the shift day this review's bonus is credited to |
| `shift_assignment_reason` | why that shift date was chosen |
| `shift_members` | who worked the credited shift (`; `-separated) |
| `trainees_on_shift` | trainees present (training-excluded in base mode) |
| `named_credit_each` | $ per named person (shoutout mode) |
| `base_credit_each` | $ per shift member (base mode) |
| `total_bonus` | total $ this review generated |
| `review_url` | link to the Google review |
| `clickup_message_id` | source ClickUp message |

**`unparseable`** — reviews that couldn't be parsed (for manual follow-up). Fields:
`clickup_message_id`, `post_ts_ms`, `post_dt_ct`, `content_preview`, `ingested_at_utc`.

### D. `bhaga_model` — derived, human-facing

Detailed field semantics for the model tabs are in §4 (labor), §5 (tips), §6 (reviews), §7
(forecast). Tab overview:

| Tab | Grain | What it answers | Fields detailed in |
|---|---|---|---|
| `config` | key/value | Operator-tunable settings (sheet IDs, store TZ, exclusions, bonus $, forecast targets, saturation threshold) | §8 |
| `daily` | per day | Quick ledger: `hours_total`, `hours_eligible_for_tip_pool`, `labor_cost_dollars`, `sales_dollars`, `labor_pct`, `tips_dollars`, `tips_per_hour`, `transaction_count`, `avg_ticket_dollars` | — |
| `labor_daily` / `labor_weekly` / `labor_period` | day / ISO week / pay period | Deep labor model: hourly vs fulltime cost, labor-% variants, throughput, saturation | §4 |
| `tip_alloc_daily` | (day, employee) | Pool-by-day fair share | §5 |
| `tip_alloc_period` | (period, employee) | Period totals + payroll reconciliation | §5 |
| `period_summary` | pay period | Period headline + diff count | §5 |
| `dow_hour` | (weekday, hour) | Trailing-28-day heatmap (`transaction_count_28d`, `sales_dollars_28d`, `tips_dollars_28d`, `avg_sales_per_day`, `avg_tips_per_day`) | — |
| `review_bonus_period` | (period, employee) | Review bonuses earned | §6 |
| ~~`labor_daily_forecast`~~ | — | **Retired 2026-06-09.** Replaced by `model_forecast_daily` in BQ. | §7 |
| `item_operations` | item line | Item-level throughput + staff punched in at sale time | §4.1 |

**`item_operations`** — one row per item line (mirrors `item_lines` grain). Key matches
`item_lines`. Built by `agents/bhaga.scripts.item_operations.build_item_operations_records`.
Upserted incrementally (not clear-and-rewrite). Money columns are in **dollars**; staff columns are
**distinct headcounts** at `item_sold_at_local`:

| Field | Meaning |
|---|---|
| `item_sold_at_local` | When the item line was recorded (shop-local); anchor for punch overlap |
| `staff_punched_in_hourly_count` | Baristas / tipped staff with an active punch at that instant |
| `staff_punched_in_fulltime_count` | Manager / excluded bucket (see §1) punched in |
| `staff_punched_in_total_count` | Sum of the two counts |

Punch rule: on `date_local`, count employee if `in_time <= item_time <= out_time` on
`bhaga_adp_raw > punches` (same buckets as `labor_daily` via `skills/bhaga_labor/staff_punched_in.py`).

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
  - **`adp_paid`** — what ADP actually paid (the "Credit Card Tips Owed" earning line). Sourced
    from **`bhaga.adp_earnings` in BigQuery** via `load_cc_tips_earnings_from_bq` — the single
    source of truth for earnings. The old GCS XLSX path is retired as a live source (kept only for
    one-off backfill tooling).
    A closed period shows `N/A` when `bhaga.adp_earnings` has no CC-tip lines for the period —
    either the period predates the backfill, **or its payroll simply hasn't run yet** (a just-closed
    period: earnings exist but carry no CC-tip lines — this is the normal pay cadence, not a defect).
  - **`diff` / `diff_pct`** — `our_calc − adp_paid`.
  - **`likely_reason`** — heuristic explanation when they diverge (open period, partial coverage, etc.).
  - **`coverage`** — how complete the period's source data is; **`is_open`** — period not yet closed/paid.
  - `period_summary.check_dates` is likewise re-derived from the parsed Earnings `check_date` values
    (was always empty after the same migration).
- **Semantic guards** (`agents/bhaga/scripts/model_semantics.py`) assert these columns stay meaningful:
  per-day tip-pool conservation, **cadence-safe** `adp_paid` reconciliation (a closed period must populate
  `adp_paid` only when `update_model_sheet.period_has_cc_tip_actuals` confirms a covering export actually
  carries that period's CC-tip lines; an unpaid just-closed period is skipped, not failed), and credited
  review bonuses survive a rebuild. They run in BOTH the per-PR sandbox e2e and the nightly
  `daily_refresh` (which trips a circuit breaker on a semantic failure).

**Tip-pool exclusions (who is dropped from the denominator).** A `(employee, date)` ruled excluded has
its hours removed from that day's tip denominator only — **labor% is unaffected** — so the pool
redistributes to everyone else. Three BQ-canonical sources, all funnelling through the single
`_is_excluded` chokepoint:

| Source | Lives in | Granularity | Meaning |
|---|---|---|---|
| `excluded_from_tip_pool` | `bhaga.store_config` (BQ) | permanent | manager/owner — never in the pool |
| `training_excluded:<name>` | `bhaga.store_config` (BQ) | through that date (inclusive) | bulk "all shifts up to date X were training" |
| **`training_shifts`** | `bhaga.training_shifts` BQ table | one `(store, employee, date)` row | precise per-shift training mark |

**BQ-canonical (post-2026-06-15 Sheets exit):** all sources live in BigQuery; no Sheet editing.
Operators use `/bhaga-cloud` Slack commands (see RUNBOOK § Exempt an employee/shift).
`training_shifts` BQ columns: `store`, `employee_name` (canonical `Last, First`), `date` (DATE),
`note`, `updated_at`, `updated_by`. View: `vw_training_shifts` (Grafana `6. Payroll` panel).
Read by the pipeline via `agents/bhaga/scripts/model_inputs.read_training_shifts()`.

`employee_aliases` BQ table (`store, raw_name, canonical_name`) replaces the Sheet `employees` tab.
The through-date shorthand and the per-shift table **coexist** — use whichever is clearer.

**Conservation invariant (machine-checked).** Pool-by-day allocation is **cent-exact (zero
tolerance)**: for every date, the per-employee allocations sum to that day's tip pool *exactly*
(largest-remainder distribution; see `skills/tip_pool_allocation/adapter.py`). The check
(`assert_tip_pool_conserved`) defaults to `tol_cents=0`, so even a 1¢/day leak fails. Every PR proves
this against real prod data over the most-recent **closed** pay period (boundaries from
`most_recent_closed_period`, the same anchor + biweekly cadence as `discover_periods`) via the per-PR
sandbox e2e (real-data rebuilds verify at max residual 0¢).

---

## 6. Review bonuses — `review_bonus_period`

Google reviews (5★ + non-empty comment) earn baristas a bonus. The allocation logic is
**date-bracketed** on the review's `post_date_ct`:

**Pool mode** (reviews posted **on/after `review_pool_effective_date`**, default `2026-06-08`):
- A fixed **$20 pool per review** is split **equally** (to the cent) among the
  **non-excluded in-hours part-time staff** clocked in at the review's post time.
- `assignment_reason` must be `"in_hours"`; reviews posted outside an active shift
  generate **$0** (no last-shift fallback).
- Permanent (`excluded_from_tip_pool`) and training (`training_through`) exclusions apply.
- Named shoutouts in the comment are **ignored** — the named person gets the same equal
  share, not a flat $20. Pool shares flow into `base_dollars`; `named_count` = 0.
- `named_baristas` column is still populated (informational only; has no effect on money).

**Legacy mode** (reviews posted **before `review_pool_effective_date`**, i.e. 2026-05-11
through 2026-06-07):
- **Shoutout mode** (review names specific staff): **only the named people** earn
  `review_named_bonus_dollars` (default $20) each. A shoutout **overrides exclusions** —
  even the tip-pool-excluded manager earns it if named.
- **Base mode** (generic 5★, no names): **every non-excluded shift member** earns
  `review_base_bonus_dollars` (default $10). Permanent + training exclusions apply.

Per-review detail (raw review tab) carries `rating`, `reviewer`, `comment`, `named_baristas`,
`shift_date_credited`, `shift_members`, `total_bonus`, etc. `review_bonus_period` rolls it up per
(period, employee): `reviews_credited`, `named_count`, `base_dollars`, `named_dollars`,
`total_bonus`. Pool shares land in `base_dollars`; `named_dollars` = 0 for pool-period rows.

Tunables in the Model `config` tab (all operator-editable in-sheet):
- `review_bonus_started_date` (default `2026-05-11`) — legacy window floor.
- `review_base_bonus_dollars` (default `10`) — legacy base mode per-person amount.
- `review_named_bonus_dollars` (default `20`) — legacy shoutout mode per-person amount.
- `review_pool_effective_date` (default `2026-06-08`) — pool mode cutover date.

**`data_window_end` — derived, never stored.** The upper bound for crediting reviews is derived
live as `MAX(square_transactions.date_local)` via `core.store_config.resolve_data_window_end()`.
It is NOT a `store_config` tunable; `set_config()` raises `ValueError` if you try to write it.
Reviews with `post_date_ct` after `data_window_end` are held back until the next run that has
Square data through that date. If reviews are unexpectedly held back, check `bhaga.store_config`
for a stale `data_window_end` row (see RUNBOOK.md §16 troubleshooting).
**`HELD-BACK: N` in the Slack summary counts only genuine review-bot posts** (messages matching the `### Google Review` header); operational chatter in the ClickUp channel (duty checklists, package photos, team messages) is excluded before the window cap check so it never inflates the counter (2026-06-25 incident, fixed).
- `review_pool_dollars` (default `20`) — total pool per qualifying review.

The rebuild is idempotent and runs every night.

---

## 6b. BigQuery model tables and Grafana views

All Sheet model tabs have corresponding BQ model tables (populated by `materialize_model_bq.py`
or `process_reviews.py`). Every raw scrape also has a 1:1 raw BQ table (mirrored nightly by
`backfill_bigquery.py`). Grafana reads from BQ views defined in `core/migrations/`.

**Migration 004 additions** (`core/migrations/004_dashboard_refactor.sql`):

| BQ table / view | Grain | Key columns | Purpose |
|---|---|---|---|
| `model_review_bonus_period` | (period_start, employee) | `reviews_credited`, `named_count`, `base_dollars`, `named_dollars`, `total_bonus` | BQ mirror of the `review_bonus_period` Sheet tab; written by `process_reviews.py` when `BHAGA_DATASTORE=bigquery`. Merge keys: (period_start, employee). **Write semantics (2026-06 hardening):** uses `replace_scope=True` in `load_model_rows` — partition values present in the batch are deleted before the MERGE so a dropped employee leaves no ghost row. Schema unchanged. |
| `vw_model_labor_daily` (extended) | day | All `model_labor_daily` cols + `labor_pct`, `hourly_pct`, `fulltime_pct` aliases | Extended view for the Grafana Labor Cost section. No view-on-view — source: `model_labor_daily`. |
| `vw_model_labor_daily` (ext 005) | day | All prior cols + `total_hours`, `hourly/fulltime_hours_per_item`, `*_hours_per_1k_net_sales` | Adds per-$1k and per-item hours ratios for the Labor section charts. |
| `vw_model_labor_weekly` (ext 005) | ISO week | All `model_labor_weekly` cols + same new Labor section cols | Same extensions as daily. Source: `model_labor_weekly`. |
| `vw_model_payroll_period` (ext 005) | (period, employee) | `hours_worked`, `est_gross_pay`, `adp_wages_paid`, `wage_diff`, `tips_allocated`, `adp_tips_paid`, `tip_diff`, `review_bonus`, `adp_bonus_paid`, `bonus_diff`, `est_total_pay`, `adp_total_paid` | Joins `model_tip_alloc_period` + `model_review_bonus_period` + `adp_wage_rates` + `adp_earnings`. ADP actuals come from `adp_earnings`; diffs = estimated − actual. |

### Raw BQ tables (migration 005 — 1:1 mirrors of scrape output)

| BQ table | Date column | Source Sheet tab | Merge keys |
|---|---|---|---|
| `square_item_lines` | `date_local` | BHAGA Square Raw > item_lines | `(transaction_id, line_seq)` |
| `square_item_daily` | `date_local` | BHAGA Square Raw > item_daily_rollup | `(date_local,)` |
| `square_kds_daily` | `date_local` | BHAGA Square Raw > kds_daily | `(date_local,)` |
| `square_kds_tickets` | `date_local` | BHAGA Square Raw > kds_tickets (NEW) | `(date_local, time_created, ticket_name)` |
| `adp_earnings` | `period_start` | BHAGA ADP Raw > earnings (NEW) | `(period_start, period_end, employee, description, check_date)` |
| `google_reviews` | `post_date_ct` | BHAGA Review Raw > reviews | `(review_id,)` |

`square_kds_tickets` and `adp_earnings` are written to BQ by the nightly pipeline: Square data via `skills/square_api/ingest.py` (REST API); ADP data via `backfill_from_downloads.py --skip square`. Their raw Sheet tabs are rendered from BQ by `render_raw_sheet_from_bq.py`.

**Raw layer is BQ-primary (PR #33, 2026-06):** scrapes land in BQ via `load_rows` (MERGE upsert); Google Sheets raw tabs are non-fatal projections rendered by `render_raw_sheet_from_bq.py`. `backfill_bigquery.py` is a one-shot historical repair tool, not the nightly path. Google Reviews follow the same path: `process_reviews.py` writes `google_reviews` to BQ; the `reviews` Sheet tab is rendered from BQ.

### Order Quality views (migration 005)

| View | Key | Columns | Notes |
|---|---|---|---|
| `vw_order_quality_daily` | date | `kds_median_min`, `kds_p90_min`, `kds_p95_min`, `kds_p99_min`, `kds_pct_items_over_goal`, `kds_pct_tickets_late` | Converts `model_labor_daily` seconds → minutes for Grafana. |
| `vw_kds_item_investigation` | (date_local, item) | `item_name`, `category`, `qty`, `per_item_min`, `ticket_min`, `device_name`, `time_created` | Explodes `items_in_ticket` from `square_kds_tickets`; `per_item_min = ROUND(completion_time_sec / num_items / 60)` (integer). category is best-effort from `square_item_lines` dimension. Delimiter: `"; "` (semicolon + space). Item format: `"<qty>x <name>"`. |
| `vw_staff_on_shift` | date | `employee`, `in_time`, `out_time`, `total_hours` | From `adp_shifts`. |
| `model_forecast_daily` | date | `date`, `forecast_orders`, `forecast_items`, `forecast_generated_at`, `forecast_model_version`, `materialized_at_utc` | BQ-authoritative daily forecast. Nightly write: **today…today+30** (today included as of v34). Past dates freeze. Merge key: `date`. Added migration 011. |
| `vw_model_forecast` | date | `date`, `dow`, `forecast_orders`, `forecast_items`, `prior_wk_orders`, `prior_wk_items`, `orders_vs_prior_wk`, `items_vs_prior_wk`, `scheduled_hours` | Forward-looking Grafana view: today + next 30 days. `dow` = day-of-week abbreviation (Mon/Tue/…, migration 015). `prior_wk_orders/items` = `IF(actual.orders > 0, actual, NULL)` COALESCE forecast@-7d — zero-order actuals (failed/closed days) fall back to that day's forecast row (migration 015). ADP scheduled hours from `adp_scheduled_daily`. |
| `vw_forecast_accuracy` | date | `date`, `forecast_orders`, `actual_orders`, `forecast_items`, `actual_items` | Accuracy series: past forecast days joined to actuals. |
| `vw_forecast_exclusions` | date | `date`, `dow`, `orders`, `prev_wk_orders`, `orders_vs_prev_wk`, `items_sold`, `prev_wk_items`, `items_vs_prev_wk`, `net_sales`, `prev_wk_net_sales`, `net_sales_vs_prev_wk`, `aov`, `prev_wk_aov`, `forecast_exclude`, `outlier_reason`, `forecast_exclude_reason` | Recent 60-day input days with exclusion flags, reasons, net-sales/AOV vs prior week (migration 014). |
| `vw_kds_order_quality_by_source_daily` | (date, order_source) | `kds_completed_tickets`, `kds_p95_min` | Per-source daily p95 of per-ITEM KDS prep time, straight from `square_kds_tickets` (migration 025). Backs panel 51's per-source chart + the `kds_source` filter variable. |

**Grafana dashboard** (`agents/bhaga/grafana/dashboard.json`) reads from these views in 5 sections: Daily Sales, Weekly Sales, Labor, Order Quality, Payroll.

**Migration 026 additions** (`core/migrations/026_review_bonus_detail.sql`):

| BQ view | Grain | Key columns | Purpose |
|---|---|---|---|
| `vw_review_bonus_detail` | one row per paid review | `post_ts_ct`, `post_date_ct`, `reviewer`, `rating`, `comment`, `review_url`, `employees_considered` (`shift_members`), `member_count`, `per_employee_bonus`, `total_bonus`, `shift_date_credited`, `shift_assignment_reason` | Per-review payroll detail. Filter: `total_bonus > 0` (only reviews that generated a bonus). `per_employee_bonus = ROUND(total_bonus / member_count, 2)`. Pool mode: equals `base_credit_each`. Backs Grafana "6. Payroll" panel "Google Reviews accounted for in Payroll". |

**Migration 027 additions** (`core/migrations/027_inventory_closing.sql`):

| BQ table / view | Grain | Key columns | Purpose |
|---|---|---|---|
| `inventory_closing_daily` | one row per (store, task, field) | `store`, `submitted_date` (DATE CT), `submitted_ts` (UTC TIMESTAMP), `source_task_id`, `category`, `item`, `field_id` (natural key deduplicator), `raw_text`, `quantity_units` (FLOAT64 normalized), `unit`, `parse_ok` (BOOL), `run_id`, `scraped_at_utc` | ClickUp "Closing" list inventory readings. Natural merge key: `(store, source_task_id, field_id)`. Partitioned by `submitted_date`. Populated by `ingest_inventory.py` (nightly non-fatal step). |
| `vw_inventory_base_latest_daily` | one row per (store, submitted_date, item) | `store`, `submitted_date`, `item`, `quantity_units`, `raw_text`, `submitted_ts`, `parse_ok` | Latest closing-form reading per base per day (`ROW_NUMBER` dedup, `category='base'`). Backs Grafana "8. Order Assistant" panel 78 (timeseries). |

**Migration 028 additions** (`core/migrations/028_inventory_order_assistant.sql`):

| BQ table / view | Grain | Key columns | Purpose |
|---|---|---|---|
| `vw_inventory_order_assistant` | one row per (store, item) | `category`, `store`, `item`, `current_qty`, `reported` (CT timestamp string), `reported_date`, `last_restock_date`, `usage_7d_total`, `avg_daily_usage`, `days_left`, `days_considered`, `excluded_days` | Analytical view for the Order Assistant table. Per-base: current stock, usage/avg/days-left over the **last 7 eligible unique-weekday transitions** (one per Mon–Sun, most recent surviving day per weekday; downward-only: `GREATEST(prev_close - curr_close, 0)`). Eligibility: previous reading exists, no submission gap (date = prev + 1), qty ≥ 1 tub, store open (orders > 0), not a restock day (curr − prev ≤ 1.0). **Two-tail outlier filter (trailing 30 days, per item):** usage is right-skewed against a floor at 0, so two instruments — (LOW) drop zero-usage days and any day `< 20%` of the nonzero median (z can't reach the low tail); (HIGH) on the low-filtered survivors compute median + MAD and drop `robust-z > 2.5` (guard: MAD > 0), mirroring `forecast.py`. Applied before the unique-weekday pick; NOT tied to restock proximity. `days_considered` = `MM/DD Ddd (x.xx)` per surviving day (consumed amount included). `excluded_days` = recent (30-day) excluded transitions with reason labels (restock X→Y, gap N days, closed, qty<1, zero usage, low outlier x.xx vs med y.yy <20%, high outlier x.xx vs med y.yy). `days_left = current_qty / avg_daily_usage` (NULL if usage = 0). Grafana panel 79 drops the `category` column (display-only; always "base" for this filter) and adds a footer row summing `Current Qty`, `Usage 7d`, `Avg per day`, and `Days Left` (the last is a raw sum of per-base runways, not a weighted aggregate). Backs Grafana "8. Order Assistant" panel 79 (analytics table). |

---

## 7. Forecast — `model_forecast_daily` (BQ-authoritative, 2026-06-09)

> **Retired:** the `labor_daily_forecast` Google Sheet tab has been replaced by a BigQuery table.
> The staffing-solver columns (`recommended_hourly_hours`, `staffing_flag`, etc.) are no longer
> produced. The BQ table stores slim forecast rows only: `date`, `forecast_orders`,
> `forecast_items`, `forecast_generated_at`. See `RUNBOOK.md §15` for operations.

The nightly pipeline (`materialize_model_bq.py`) calls `forecast_bq.build_forecast_rows()` after
writing `model_labor_daily`, then loads the result into `model_forecast_daily` with
`merge_keys=["date"]`. **Forward rows** (today…today+30) are merged each nightly run, updating until the day passes. **Today is included** so next week's panel-71 `prior_wk_orders` always has a non-null fallback for the current day. **Past rows** are gap-fill-only: the nightly pipeline reads existing past dates first and skips them, freezing history across model updates. Each row is stamped with `forecast_model_version` (e.g. `"wow_median_4wk_v2"`) for accuracy attribution.

**Growth model (wow_median_4wk_v2, 2026-06-10):** `growth = median` of consecutive same-weekday WoW order ratios over the trailing 28 days, clamped [0.80, 1.20]. Each ratio = orders[d] / orders[d-7] for matching weekdays. Robust to one anomalous week (median ignores a 2.3× holiday spike when 18 other pairs are normal). Returns 1.0 when <2 valid pairs. Prod value: ~+2.7%/wk.

Three Grafana views expose the data: `vw_model_forecast` (forward-looking with prior-week comparison + ADP scheduled hours), `vw_forecast_accuracy` (past forecast vs actual), `vw_forecast_exclusions` (recent input days with `forecast_exclude` flag + net-sales/AOV vs prior week).

**`forecast_exclude` override** — set `forecast_exclude = TRUE` on a `model_labor_daily` row to drop that day from the forecast seed. The outlier detector auto-sets this for two signal types: (1) order-volume DOWN outliers (robust-z < −2.5 with negative residual vs trend expectation); (2) **AOV down outliers** (Average Order Value = net_sales/orders robust-z < −2.5, detecting comped/discounted days). Both are computed in `compute_outlier_stats` (forecast.py). The forecaster calls `_get_parsed_rows(exclude_flagged=True)`.

### Legacy doc (pre-2026-06-09)

Previously a **live, in-sheet planning tool**: future rows where derived columns are Google Sheets
formulas. Inputs (editable per row): `orders`, `fulltime_hours`, `target_labor_pct`,
`target_hourly_labor_pct`, `target_time_per_item_sec`, `forecast_exclude`.

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

## 8. Growing the data model — the two directions

Data grows in exactly two directions. Know which one you're doing, because they touch different code.

```
  Direction 1 (capture more)            Direction 2 (derive more)
  Square / ADP / reviews                BQ raw tables (→ Sheet projection)
        │  scrape backend                     │  reader.py / BQ query
        ▼                                     ▼
  BQ raw tables ──────────────────────▶ build_*_rows() ───▶ model tabs
  (bhaga dataset;                                                ▲
   11 tables via              raw Sheets (projection)            │
   backfill_from_downloads)        ▲                  add a NEW derived column/tab here
        ▲                          │ render_raw_sheet_from_bq
    add a NEW raw field here       └─────────────────────────
                                    (non-fatal; BQ is authoritative)
```

### Direction 1 — pull a NEW field straight from a source (source → raw sheet)

Use when the data you want **isn't scraped yet** (e.g. a new Square column, an ADP field, a review
attribute). You must teach the scrape backend to emit it, then widen the raw tab:

1. **Emit the field in the scrape/API backend** — Square: `skills/square_api/export.py` (transactions/items) or `skills/square_api/kds_reporting.py` (KDS), with in-memory row dicts passed to `skills/square_tips/transactions_backend.parse_transaction_rows` / `parse_kds_dictrows`;
   ADP: `skills/adp_run_automation/shift_backend.py` or `compensation_backend.py`; reviews:
   the parser in `agents/bhaga/scripts/process_reviews.py`.
2. **Append the column to the raw tab's header** — ADP/Square in `skills/tip_ledger_writer/schema.py`
   (`WORKBOOK_SCHEMAS`); reviews in the `*_HEADER_ROW` constants in `process_reviews.py`. **Append at
   the end** — additive changes auto-migrate the live sheet.
3. **Backfill** — re-scrape the window so the new column populates history
   (`backfill_from_downloads.py` / re-run the gap window). Old rows stay blank for the new column
   until re-scraped.
4. **Then optionally surface it in the model** (Direction 2) and **document it in this file** (§3).

> Most "add a field" requests are actually **Direction 2** — the raw sheets already capture more than
> the model surfaces. Check the §3 field lists first; if the field is already in a raw tab, skip
> straight to Direction 2 (no scraping change needed).

### Direction 2 — derive NEW info in the model from the raw sheets (raw → model)

Use when the raw data already exists and you just want a new derived column or tab. Read raw via
`skills/tip_ledger_writer/reader.py` (the typed `read_raw_*` catalog — see scripts/README), compute
in a `build_*_rows` function, and let the upsert path handle idempotency + header migration.

**Step-by-step code recipes for both directions live in
[`../scripts/README.md`](../scripts/README.md) § Extending the model** (Recipe A = add a column,
Recipe B = new derived tab, Recipe C = new field from source). This file tells you *what the data
means*; that file tells you *how to wire it*.

### Just tuning a number?

Bonus amounts, forecast targets, saturation threshold, exclusions, etc. live in the Model `config`
tab — **edit them in-sheet, no code change**.

### Keep this dictionary current

When columns / metrics / domain meaning change, update §3 / §4–7 here in the same change (the
`doc-maintenance` rule + `check_doc_freshness.py` will remind you).

