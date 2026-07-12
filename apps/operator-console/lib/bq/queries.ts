import "server-only";
import { dateParam, fq, intParam, q } from "./client";
import { bucketSql, type DateWindow, type Grain } from "@/lib/filters/range";

// Column names/units verified against core/migrations/005_raw_parity.sql
// (vw_model_labor_daily) and agents/bhaga/knowledge-base/DOMAIN.md — money
// here is dollars-and-cents float, not integer cents. Never guess a name.
export interface LaborDailyRow {
  date: string;
  dow: string;
  net_sales: number;
  total_labor_cost: number;
  labor_pct: number;
  hourly_pct: number;
  fulltime_pct: number;
  total_hours: number;
  hours_per_item: number;
  orders: number;
  items_sold: number;
  [key: string]: unknown;
}

// vw_model_labor_daily predates multi-store (core/migrations/003/005) and has
// no `store` column — it is implicitly Austin/palmetto today. Only the newer
// tables (store_config, training_shifts, inventory_*, pipeline_runs,
// source_pulls) carry a real `store` key; do not add a WHERE store= filter
// here until a second store's data actually lands in this table.
export function laborDaily(win: DateWindow): Promise<LaborDailyRow[]> {
  return q<LaborDailyRow>(
    `SELECT * FROM ${fq("vw_model_labor_daily")}
     WHERE date BETWEEN @start AND @end
     ORDER BY date DESC`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

// Sales/Labor pages' grain-aware reader (Issue #132 follow-up). `dow` is
// meaningless once multiple days are collapsed into one week/month bucket,
// so it's only populated at day grain — pages drop the "Day" column for
// week/month. Every ratio is *recomputed* from summed numerators/
// denominators (never averaged pre-computed daily ratios), so e.g. a
// month's `labor_pct` is (sum of labor cost)/(sum of net sales) — the same
// number you'd get hand-computing it from the raw rows, not an average of
// daily percentages that would misweight low-volume days. `laborDaily`
// above is untouched and still backs the Home scorecard (day-grain only,
// out of scope for the grain picker).
export function laborByGrain(win: DateWindow, grain: Grain): Promise<LaborDailyRow[]> {
  const bucket = bucketSql(grain);
  const dow = grain === "day" ? "ANY_VALUE(dow)" : "CAST(NULL AS STRING)";
  return q<LaborDailyRow>(
    `SELECT
       ${bucket} AS date,
       ${dow} AS dow,
       SUM(net_sales) AS net_sales,
       SUM(total_labor_cost) AS total_labor_cost,
       SAFE_DIVIDE(SUM(total_labor_cost), SUM(net_sales)) AS labor_pct,
       SAFE_DIVIDE(SUM(hourly_labor_cost), SUM(net_sales)) AS hourly_pct,
       SAFE_DIVIDE(SUM(fulltime_labor_cost), SUM(net_sales)) AS fulltime_pct,
       SUM(hourly_hours) + SUM(fulltime_hours) AS total_hours,
       SUM(hourly_hours) AS hourly_hours,
       SUM(fulltime_hours) AS fulltime_hours,
       SAFE_DIVIDE(SUM(hourly_hours) + SUM(fulltime_hours), SUM(items_sold)) AS hours_per_item,
       SAFE_DIVIDE(SUM(hourly_hours), SUM(items_sold)) AS hourly_hours_per_item,
       SAFE_DIVIDE(SUM(fulltime_hours), SUM(items_sold)) AS fulltime_hours_per_item,
       SUM(orders) AS orders,
       SUM(items_sold) AS items_sold,
       SAFE_DIVIDE(SUM(net_sales), SUM(orders)) AS avg_order_price
     FROM ${fq("vw_model_labor_daily")}
     WHERE date BETWEEN @start AND @end
     GROUP BY date
     ORDER BY date DESC`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

export interface LaborWeeklyRow {
  iso_week: string;
  week_start: string;
  week_end: string;
  is_partial: boolean;
  net_sales: number;
  total_labor_cost: number;
  labor_pct: number;
  total_hours: number;
  orders: number;
  [key: string]: unknown;
}

export function laborWeekly(weeks = 12): Promise<LaborWeeklyRow[]> {
  return q<LaborWeeklyRow>(
    `SELECT * FROM ${fq("vw_model_labor_weekly")}
     ORDER BY week_start DESC LIMIT @weeks`,
    { weeks },
  );
}

export interface ItemDailyRow {
  date_local: string;
  items_sold: number;
  units_sold: number;
  gross_sales_cents: number;
  avg_item_price_cents: number;
  [key: string]: unknown;
}

// square_item_daily is a raw table (money in cents — see DOMAIN.md §6a), not a
// vw_* view; still store-implicit like the model_* tables above.
export function salesItemDaily(days = 30): Promise<ItemDailyRow[]> {
  return q<ItemDailyRow>(
    `SELECT * FROM ${fq("square_item_daily")}
     WHERE date_local >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date_local DESC`,
    { days },
  );
}

export interface ForecastRow {
  date: string;
  dow: string;
  forecast_orders: number;
  forecast_items: number;
  prior_wk_orders: number;
  prior_wk_items: number;
  orders_vs_prior_wk: number;
  items_vs_prior_wk: number;
  scheduled_hours: number;
  [key: string]: unknown;
}

// Forecast rows exist for dates >= the pipeline's run date, so a past-only
// preset (e.g. last_month) legitimately returns no rows here — the caller
// renders an empty state rather than this silently falling back to "today".
//
// Grain-aware version (Issue #132 follow-up): sums the volume columns, then
// *recomputes* `orders_vs_prior_wk`/`items_vs_prior_wk` from the summed
// forecast/prior-week totals — never averages the daily ratios, which would
// misweight low-volume days the same way `laborByGrain` avoids for labor%.
// `dow` is day-grain-only, same rationale as `laborByGrain`.
export function forecastByGrain(win: DateWindow, grain: Grain): Promise<ForecastRow[]> {
  const bucket = bucketSql(grain);
  const dow = grain === "day" ? "ANY_VALUE(dow)" : "CAST(NULL AS STRING)";
  return q<ForecastRow>(
    `SELECT
       ${bucket} AS date,
       ${dow} AS dow,
       SUM(forecast_orders) AS forecast_orders,
       SUM(forecast_items) AS forecast_items,
       SUM(prior_wk_orders) AS prior_wk_orders,
       SUM(prior_wk_items) AS prior_wk_items,
       SAFE_DIVIDE(SUM(forecast_orders) - SUM(prior_wk_orders), NULLIF(SUM(prior_wk_orders), 0)) AS orders_vs_prior_wk,
       SAFE_DIVIDE(SUM(forecast_items) - SUM(prior_wk_items), NULLIF(SUM(prior_wk_items), 0)) AS items_vs_prior_wk,
       SUM(scheduled_hours) AS scheduled_hours
     FROM ${fq("vw_model_forecast")}
     WHERE date BETWEEN @start AND @end
     GROUP BY date
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

export interface ForecastExclusionRow {
  date: string;
  dow: string;
  orders: number;
  items_sold: number;
  prev_wk_orders: number;
  prev_wk_items: number;
  orders_vs_prev_wk: number;
  items_vs_prev_wk: number;
  net_sales: number;
  prev_wk_net_sales: number;
  net_sales_vs_prev_wk: number;
  aov: number;
  prev_wk_aov: number;
  forecast_exclude: boolean;
  // "excluded" | "success" — pre-mapped to DataTable's `status` format
  // convention (see app/pipeline/page.tsx's StatusBadge: "success" ->
  // default/green, any other truthy string -> destructive/red) so the raw
  // BOOLEAN never has to round-trip through the client component as-is
  // (a bare `true`/`false` renders as nothing in a Badge's children).
  excluded_status: "excluded" | "success";
  outlier_reason: string | null;
  forecast_exclude_reason: string | null;
  [key: string]: unknown;
}

// Grafana panel 84 parity ("Forecast Inputs / Exclusions") — read-only in
// the console; the `forecast_exclude` override itself stays a BQ-tab edit,
// same as Grafana (out of scope — see plan's "Out of scope" section).
// `vw_forecast_exclusions` already caps itself to the last 60 days
// (migration 014), so `days` only ever narrows that window further.
export function forecastExclusions(days = 60): Promise<ForecastExclusionRow[]> {
  return q<ForecastExclusionRow>(
    `SELECT
       *,
       IF(forecast_exclude, 'excluded', 'success') AS excluded_status
     FROM ${fq("vw_forecast_exclusions")}
     WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date DESC`,
    { days: intParam(days) },
  );
}

export interface ForecastAccuracyRow {
  date: string;
  forecast_orders: number;
  actual_orders: number;
  forecast_items: number;
  actual_items: number;
  [key: string]: unknown;
}

export function forecastAccuracyByGrain(win: DateWindow, grain: Grain): Promise<ForecastAccuracyRow[]> {
  const bucket = bucketSql(grain);
  return q<ForecastAccuracyRow>(
    `SELECT
       ${bucket} AS date,
       SUM(forecast_orders) AS forecast_orders,
       SUM(actual_orders) AS actual_orders,
       SUM(forecast_items) AS forecast_items,
       SUM(actual_items) AS actual_items
     FROM ${fq("vw_forecast_accuracy")}
     WHERE date BETWEEN @start AND @end
     GROUP BY date
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

export interface OrderQualityDailyRow {
  date: string;
  kds_median_min: number;
  kds_p90_min: number;
  kds_p95_min: number;
  kds_p99_min: number;
  kds_pct_items_over_goal: number;
  kds_pct_tickets_late: number;
  [key: string]: unknown;
}

export function orderQualityDaily(win: DateWindow): Promise<OrderQualityDailyRow[]> {
  return q<OrderQualityDailyRow>(
    `SELECT * FROM ${fq("vw_order_quality_daily")}
     WHERE date BETWEEN @start AND @end
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

export interface KdsBySourceRow {
  date: string;
  order_source: string;
  kds_completed_tickets: number;
  kds_p95_min: number;
  [key: string]: unknown;
}

export function kdsBySource(win: DateWindow): Promise<KdsBySourceRow[]> {
  return q<KdsBySourceRow>(
    `SELECT * FROM ${fq("vw_kds_order_quality_by_source_daily")}
     WHERE date BETWEEN @start AND @end
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

// Grafana parity (Issue #132 follow-up): neither `orderQualityDaily` (derives
// percentiles from pre-collapsed per-item columns in model_labor_daily, no
// order_source) nor `kdsBySource` (already one-row-per-day+source, can't be
// re-aggregated into weeks/months) can serve a grain-aware, Source-filtered
// percentile view — both read pre-collapsed daily columns. This reads
// migration 034's `vw_kds_per_item_min` (raw per-ticket ratio) so
// APPROX_QUANTILES can run fresh at any GROUP BY <bucket>, with Source
// applied inside the same query (not a client-side post-filter). `source`
// is bound as a param, never interpolated — `'All'` means "no filter" via
// the `@source = 'All' OR order_source = @source` guard rather than
// building the SQL string conditionally.
export function orderQualityByGrain(
  win: DateWindow,
  grain: Grain,
  source: string,
  onTime: number,
): Promise<OrderQualityDailyRow[]> {
  const bucket = bucketSql(grain);
  return q<OrderQualityDailyRow>(
    `SELECT
       ${bucket} AS date,
       COUNT(*) AS kds_completed_tickets,
       APPROX_QUANTILES(per_item_min, 100)[OFFSET(50)] AS kds_median_min,
       APPROX_QUANTILES(per_item_min, 100)[OFFSET(90)] AS kds_p90_min,
       APPROX_QUANTILES(per_item_min, 100)[OFFSET(95)] AS kds_p95_min,
       APPROX_QUANTILES(per_item_min, 100)[OFFSET(99)] AS kds_p99_min,
       SAFE_DIVIDE(COUNTIF(per_item_min > @onTime), COUNT(*)) AS kds_pct_items_over_goal
     FROM ${fq("vw_kds_per_item_min")}
     WHERE date BETWEEN @start AND @end
       AND (@source = 'All' OR order_source = @source)
     GROUP BY date
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end), source, onTime },
  );
}

export interface KdsOrderInvestigationRow {
  date_local: string;
  ticket_name: string;
  order_source: string;
  start_time: string;
  end_time: string;
  num_items: number;
  order_min: number;
  min_per_item: number;
  staff_on_shift: string | null;
  items_in_ticket: string;
  [key: string]: unknown;
}

// The missing Grafana "Order KDS Times" investigation table (panel 52) —
// same shape/threshold semantics, but date-range-driven (BETWEEN @start AND
// @end) rather than Grafana's single-date `$kds_date` picker, matching every
// other console table's date-range convention.
export function kdsOrderInvestigation(
  win: DateWindow,
  source: string,
  minPerItem: number,
): Promise<KdsOrderInvestigationRow[]> {
  return q<KdsOrderInvestigationRow>(
    `SELECT
       o.date_local,
       o.ticket_name,
       o.order_source,
       o.start_time,
       o.end_time,
       o.num_items,
       o.order_min,
       ROUND(o.order_min / o.num_items, 1) AS min_per_item,
       (
         SELECT STRING_AGG(DISTINCT s.employee, ' | ' ORDER BY s.employee)
         FROM ${fq("vw_staff_on_shift")} s
         WHERE s.date = o.date_local
           AND SAFE.PARSE_TIME('%H:%M', s.in_time) <= SAFE.PARSE_TIME('%H:%M:%S', o.end_time)
           AND SAFE.PARSE_TIME('%H:%M', s.out_time) >= SAFE.PARSE_TIME('%H:%M:%S', o.start_time)
       ) AS staff_on_shift,
       o.items_in_ticket
     FROM ${fq("vw_kds_order_investigation")} o
     WHERE o.date_local BETWEEN @start AND @end
       AND (@source = 'All' OR o.order_source = @source)
       AND ROUND(o.order_min / o.num_items, 1) >= @minPerItem
     ORDER BY min_per_item DESC, o.ticket_name`,
    { start: dateParam(win.start), end: dateParam(win.end), source, minPerItem },
  );
}

export interface PayrollPeriodRow {
  period_start: string;
  period_end: string;
  is_open: boolean;
  employee: string;
  hours_worked: number;
  est_gross_pay: number;
  tips_allocated: number;
  review_bonus: number;
  est_total_pay: number;
  adp_wages_paid: number;
  adp_tips_paid: number;
  adp_bonus_paid: number;
  adp_total_paid: number;
  wage_diff: number;
  tip_diff: number;
  bonus_diff: number;
  [key: string]: unknown;
}

export function payrollPeriod(periods = 2): Promise<PayrollPeriodRow[]> {
  return q<PayrollPeriodRow>(
    `SELECT * FROM ${fq("vw_model_payroll_period")}
     WHERE period_start >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @periods * 15 DAY)
     ORDER BY period_start DESC, employee`,
    { periods },
  );
}

export interface ReviewBonusDetailRow {
  post_date_ct: string;
  reviewer: string;
  rating: number;
  total_bonus: number;
  per_employee_bonus: number;
  employees_considered: string;
  shift_date_credited: string;
  [key: string]: unknown;
}

export function reviewBonusDetail(days = 30): Promise<ReviewBonusDetailRow[]> {
  return q<ReviewBonusDetailRow>(
    `SELECT * FROM ${fq("vw_review_bonus_detail")}
     WHERE post_date_ct >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY post_date_ct DESC`,
    { days },
  );
}

export interface PipelineRunRow {
  run_id: string;
  run_date: string;
  started_at_utc: string;
  finished_at_utc: string;
  runtime_s: number;
  status: string;
  failed_step: string | null;
  error: string | null;
  recovery_retrigger: boolean | null;
  [key: string]: unknown;
}

export function pipelineRuns(): Promise<PipelineRunRow[]> {
  return q<PipelineRunRow>(`SELECT * FROM ${fq("vw_pipeline_runs")}`);
}

export interface SourcePullRow {
  run_id: string;
  run_date: string;
  source: string;
  started_at_utc: string;
  finished_at_utc: string;
  status: string;
  error: string | null;
  [key: string]: unknown;
}

export function sourcePulls(): Promise<SourcePullRow[]> {
  return q<SourcePullRow>(`SELECT * FROM ${fq("vw_source_pulls")}`);
}

export interface StoreConfigRow {
  store: string;
  key: string;
  value: string;
  notes: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export function storeConfig(store: string): Promise<StoreConfigRow[]> {
  return q<StoreConfigRow>(
    `SELECT * FROM ${fq("store_config")} WHERE store=@store ORDER BY key`,
    { store },
  );
}

export interface OrderAssistantRow {
  Item: string;
  "Current Qty": number;
  Reported: string;
  "Last Restock": string | null;
  "Usage 7d": number;
  "Avg per day": number;
  "Days Left": number | null;
  "Days Considered": string;
  Exclusions: string | null;
  [key: string]: unknown;
}

// M2 placeholder read (single store, filtered inside the view — see 028/029
// migrations). M3 replaces this page with vw_order_reco_combined's dual-date
// table; this keeps the nav item real in the meantime.
export function orderAssistantTable(): Promise<OrderAssistantRow[]> {
  return q<OrderAssistantRow>(`SELECT * FROM ${fq("vw_order_assistant_table")}`);
}

// vw_order_reco_combined (migration 032) — one row per Item, date-qualified
// "N" suffix columns for slot 1/2. Hardcoded to store='palmetto' inside the
// view itself (Issue #137, single-store today); no store param here to match.
export interface OrderRecoCombinedRow {
  Item: string;
  "Current Qty": number;
  "Avg per day": number;
  "On Hand 1": number | null;
  "Order Tubs 1": number | null;
  "Order Weight 1": number | null;
  "After Restock 1": number | null;
  "Days Left 1": number | null;
  "Source 1": "Estimated" | "Actuals" | null;
  "On Hand 2": number | null;
  "Order Tubs 2": number | null;
  "Order Weight 2": number | null;
  "After Restock 2": number | null;
  "Days Left 2": number | null;
  "Source 2": "Estimated" | "Actuals" | null;
  _ord: number;
  refresh_date: string | null;
  [key: string]: unknown;
}

export function orderRecoCombined(): Promise<OrderRecoCombinedRow[]> {
  return q<OrderRecoCombinedRow>(`SELECT * FROM ${fq("vw_order_reco_combined")}`);
}

// vw_order_reco_next_dates (migration 031) — the next 2 future registered
// delivery dates, slot 1 = sooner. Empty/short when fewer dates are registered.
export interface NextDateRow {
  delivery_date: string;
  slot: number;
}

export function nextDates(): Promise<NextDateRow[]> {
  return q<NextDateRow>(`SELECT * FROM ${fq("vw_order_reco_next_dates")} ORDER BY slot`);
}

/** Future schedule dates with no actuals (Estimated-only) — for Replace estimated date. */
export interface EstimatedScheduleDateRow {
  delivery_date: string;
}

export function estimatedScheduleDates(store: string): Promise<EstimatedScheduleDateRow[]> {
  return q<EstimatedScheduleDateRow>(
    `SELECT s.delivery_date
     FROM ${fq("inventory_restock_schedule")} s
     LEFT JOIN (
       SELECT DISTINCT delivery_date
       FROM ${fq("inventory_restock_orders")}
       WHERE store = @store
     ) o ON s.delivery_date = o.delivery_date
     WHERE s.store = @store
       AND s.delivery_date >= CURRENT_DATE('America/Chicago')
       AND o.delivery_date IS NULL
     ORDER BY s.delivery_date`,
    { store },
  );
}

// vw_inventory_base_runway (migration 035, Issue #156) — burn-down days left
// from today, Actuals-only next restock, Risky/Fine status. Console-only.
export interface BaseRunwayRow {
  Base: string;
  Stock: number;
  "Vel per day": number;
  "Days left": number | null;
  "Stockout date": string | null;
  "Next restock": string | null;
  "Restock qty": number | null;
  Status: "Risky" | "Fine";
  [key: string]: unknown;
}

export function baseRunway(): Promise<BaseRunwayRow[]> {
  return q<BaseRunwayRow>(`SELECT * FROM ${fq("vw_inventory_base_runway")}`);
}

// training_shifts (migration 011-era table, written by /bhaga-cloud
// `training set`/`training rm` and now also the console's quick-add — M4).
export interface TrainingShiftRow {
  employee_name: string;
  date: string;
  note: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export function trainingShifts(store: string, days: number): Promise<TrainingShiftRow[]> {
  return q<TrainingShiftRow>(
    `SELECT employee_name, date, note, updated_by, updated_at FROM ${fq("training_shifts")}
     WHERE store=@store AND date >= DATE_SUB(CURRENT_DATE("America/Chicago"), INTERVAL @days DAY)
     ORDER BY date DESC`,
    { store, days },
  );
}

// recognition_bonuses (migration 033) — manual per-employee bonus, distinct
// from the automated vw_review_bonus_detail (migration 026).
export interface RecognitionBonusRow {
  pay_period: string;
  employee: string;
  amount_cents: number;
  reason: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export function recognitionBonuses(store: string, periods = 4): Promise<RecognitionBonusRow[]> {
  return q<RecognitionBonusRow>(
    `SELECT pay_period, employee, amount_cents, reason, updated_by, updated_at
     FROM ${fq("recognition_bonuses")} WHERE store=@store
     ORDER BY pay_period DESC LIMIT @limit`,
    { store, limit: intParam(periods * 20) },
  );
}
