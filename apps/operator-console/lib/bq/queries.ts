import "server-only";
import { fq, intParam, q } from "./client";

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
export function laborDaily(days = 30): Promise<LaborDailyRow[]> {
  return q<LaborDailyRow>(
    `SELECT * FROM ${fq("vw_model_labor_daily")}
     WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date DESC`,
    { days },
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

export function forecast(days = 30): Promise<ForecastRow[]> {
  return q<ForecastRow>(
    `SELECT * FROM ${fq("vw_model_forecast")}
     WHERE date <= DATE_ADD(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date`,
    { days },
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

export function forecastAccuracy(days = 30): Promise<ForecastAccuracyRow[]> {
  return q<ForecastAccuracyRow>(
    `SELECT * FROM ${fq("vw_forecast_accuracy")}
     WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date`,
    { days },
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

export function orderQualityDaily(days = 30): Promise<OrderQualityDailyRow[]> {
  return q<OrderQualityDailyRow>(
    `SELECT * FROM ${fq("vw_order_quality_daily")}
     WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date`,
    { days },
  );
}

export interface KdsBySourceRow {
  date: string;
  order_source: string;
  kds_completed_tickets: number;
  kds_p95_min: number;
  [key: string]: unknown;
}

export function kdsBySource(days = 30): Promise<KdsBySourceRow[]> {
  return q<KdsBySourceRow>(
    `SELECT * FROM ${fq("vw_kds_order_quality_by_source_daily")}
     WHERE date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL @days DAY)
     ORDER BY date`,
    { days },
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
