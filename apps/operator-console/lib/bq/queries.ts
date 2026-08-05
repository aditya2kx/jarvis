import "server-only";
import { dateParam, fq, intParam, q } from "./client";
import { bucketSql, type DateWindow, type Grain } from "@/lib/filters/range";
import {
  computeLaborForwardSummary,
  type LaborForwardSummary,
} from "@/lib/kpi/labor-forward";

export type { LaborForwardSummary };

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

/** Hours worked per person over the console Period (ADP shifts; Issue #213 L3). */
export interface LaborHoursPerPersonRow {
  employee: string;
  hours: number;
  [key: string]: unknown;
}

export function laborHoursPerPerson(win: DateWindow): Promise<LaborHoursPerPersonRow[]> {
  return q<LaborHoursPerPersonRow>(
    `SELECT
       COALESCE(NULLIF(TRIM(canonical_name), ''), employee_id) AS employee,
       SUM(total_hours) AS hours
     FROM ${fq("adp_shifts")}
     WHERE date BETWEEN @start AND @end
     GROUP BY employee
     HAVING hours > 0
     ORDER BY hours DESC`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

/**
 * Average concurrent staff on each day (Issue #213).
 *
 * Per bucket (PT / FT): Σ hours ÷ (first in → last out) **within that bucket**
 * so one full-timer ≈ 1.0, not diluted by a longer store-open span from PT.
 * Total still uses the all-staff span. Week/month = AVG of daily values.
 * Matches scheduled concurrent (`aggregateScheduledDays` wall-clock ranges).
 */
export interface LaborConcurrentRow {
  date: string;
  parttime_concurrent: number;
  fulltime_concurrent: number;
  total_concurrent: number;
  [key: string]: unknown;
}

export function laborConcurrentByGrain(
  win: DateWindow,
  grain: Grain,
): Promise<LaborConcurrentRow[]> {
  const bucket = bucketSql(grain, "date");
  return q<LaborConcurrentRow>(
    `WITH shifts AS (
       SELECT
         s.date,
         s.total_hours,
         SAFE.PARSE_TIME('%H:%M', s.in_time) AS tin,
         SAFE.PARSE_TIME('%H:%M', s.out_time) AS tout,
         IF(
           IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
           'fulltime',
           'parttime'
         ) AS labor_bucket
       FROM ${fq("adp_shifts")} s
       LEFT JOIN ${fq("adp_wage_rates")} w
         ON w.employee_id = s.employee_id
       WHERE s.date BETWEEN @start AND @end
         AND IFNULL(s.total_hours, 0) > 0
     ),
     daily AS (
       SELECT
         date,
         SUM(IF(labor_bucket = 'parttime', total_hours, 0)) AS pt_hours,
         SUM(IF(labor_bucket = 'fulltime', total_hours, 0)) AS ft_hours,
         SAFE_DIVIDE(
           TIME_DIFF(
             MAX(IF(labor_bucket = 'parttime', tout, NULL)),
             MIN(IF(labor_bucket = 'parttime', tin, NULL)),
             SECOND
           ),
           3600.0
         ) AS pt_span,
         SAFE_DIVIDE(
           TIME_DIFF(
             MAX(IF(labor_bucket = 'fulltime', tout, NULL)),
             MIN(IF(labor_bucket = 'fulltime', tin, NULL)),
             SECOND
           ),
           3600.0
         ) AS ft_span,
         SAFE_DIVIDE(TIME_DIFF(MAX(tout), MIN(tin), SECOND), 3600.0) AS all_span
       FROM shifts
       WHERE tin IS NOT NULL AND tout IS NOT NULL AND tout > tin
       GROUP BY date
     ),
     daily_avg AS (
       SELECT
         date,
         SAFE_DIVIDE(pt_hours, pt_span) AS parttime_concurrent,
         SAFE_DIVIDE(ft_hours, ft_span) AS fulltime_concurrent,
         SAFE_DIVIDE(pt_hours + ft_hours, all_span) AS total_concurrent
       FROM daily
       WHERE all_span > 0
     )
     SELECT
       ${bucket} AS date,
       AVG(parttime_concurrent) AS parttime_concurrent,
       AVG(fulltime_concurrent) AS fulltime_concurrent,
       AVG(total_concurrent) AS total_concurrent
     FROM daily_avg
     GROUP BY date
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

/**
 * Scheduled hours by Aggregation grain for today+ (ADP Team Schedule).
 * PT/FT from adp_wage_rates (salaried / excluded_from_labor_pct → FT).
 * No net sales / labor % — callers must not invent %.
 */
export interface LaborScheduledHoursRow {
  date: string;
  parttime_hours: number;
  fulltime_hours: number;
  total_hours: number;
  [key: string]: unknown;
}

export function laborScheduledHoursByGrain(
  win: DateWindow,
  grain: Grain,
  opts?: { excludePto?: boolean },
): Promise<LaborScheduledHoursRow[]> {
  const bucket = bucketSql(grain, "s.date");
  const ptoClause = opts?.excludePto
    ? `AND IFNULL(s.hour_kind, 'shift') != 'pto'`
    : "";
  return q<LaborScheduledHoursRow>(
    `SELECT
       ${bucket} AS date,
       SUM(IF(
         IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
         0,
         s.scheduled_hours
       )) AS parttime_hours,
       SUM(IF(
         IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
         s.scheduled_hours,
         0
       )) AS fulltime_hours,
       SUM(s.scheduled_hours) AS total_hours
     FROM ${fq("adp_scheduled_shifts")} s
     LEFT JOIN ${fq("adp_wage_rates")} w
       ON w.employee_id = s.employee_id
     WHERE s.date BETWEEN @start AND @end
       AND s.date >= CURRENT_DATE('America/Chicago')
       AND IFNULL(s.scheduled_hours, 0) > 0
       ${ptoClause}
     GROUP BY date
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

/** Day-level scheduled rows for concurrent + coverage swimlanes (ranges in TS). */
export interface LaborScheduledShiftDayRow {
  date: string;
  employee: string;
  labor_bucket: string;
  scheduled_hours: number;
  shift_ranges_json: string | null;
  [key: string]: unknown;
}

export function laborScheduledShiftDays(
  win: DateWindow,
  opts?: { excludePto?: boolean },
): Promise<LaborScheduledShiftDayRow[]> {
  const ptoClause = opts?.excludePto
    ? `AND IFNULL(s.hour_kind, 'shift') != 'pto'`
    : "";
  return q<LaborScheduledShiftDayRow>(
    `SELECT
       CAST(s.date AS STRING) AS date,
       COALESCE(NULLIF(TRIM(s.employee_name), ''), s.employee_id) AS employee,
       IF(
         IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
         'fulltime',
         'parttime'
       ) AS labor_bucket,
       s.scheduled_hours,
       s.shift_ranges_json
     FROM ${fq("adp_scheduled_shifts")} s
     LEFT JOIN ${fq("adp_wage_rates")} w
       ON w.employee_id = s.employee_id
     WHERE s.date BETWEEN @start AND @end
       AND s.date >= CURRENT_DATE('America/Chicago')
       AND IFNULL(s.scheduled_hours, 0) > 0
       ${ptoClause}
     ORDER BY date, employee`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

/** Day-level clocked shifts for coverage swimlanes (Issue #213). */
export interface LaborActualShiftDayRow {
  date: string;
  employee: string;
  labor_bucket: string;
  in_time: string | null;
  out_time: string | null;
  total_hours: number;
  [key: string]: unknown;
}

export function laborActualShiftDays(
  win: DateWindow,
): Promise<LaborActualShiftDayRow[]> {
  return q<LaborActualShiftDayRow>(
    `SELECT
       CAST(s.date AS STRING) AS date,
       COALESCE(NULLIF(TRIM(s.canonical_name), ''), s.employee_id) AS employee,
       IF(
         IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE),
         'fulltime',
         'parttime'
       ) AS labor_bucket,
       s.in_time,
       s.out_time,
       s.total_hours
     FROM ${fq("adp_shifts")} s
     LEFT JOIN ${fq("adp_wage_rates")} w
       ON w.employee_id = s.employee_id
     WHERE s.date BETWEEN @start AND @end
       AND s.date < CURRENT_DATE('America/Chicago')
       AND IFNULL(s.total_hours, 0) > 0
     ORDER BY date, employee`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

/** Max scraped_at for schedule tables (Sync button freshness). */
export function adpScheduleScrapedAt(): Promise<string | null> {
  return q<{ scraped: string | null }>(
    `SELECT CAST(MAX(scraped_at_utc) AS STRING) AS scraped
     FROM ${fq("adp_scheduled_shifts")}`,
  ).then((rows) => rows[0]?.scraped ?? null);
}

/** Latest calendar date with scheduled hours (today+), for chart horizon. */
export function adpScheduleHorizonEnd(): Promise<string | null> {
  return q<{ horizon: string | null }>(
    `SELECT CAST(MAX(date) AS STRING) AS horizon
     FROM ${fq("adp_scheduled_shifts")}
     WHERE date >= CURRENT_DATE('America/Chicago')
       AND IFNULL(scheduled_hours, 0) > 0`,
  ).then((rows) => {
    const h = rows[0]?.horizon;
    return h ? String(h).slice(0, 10) : null;
  });
}

// Sales page source filter (#198). Reads square_transactions (+ item_lines for
// items_sold) so we can filter/group by Square `source`. Unfiltered totals
// reconcile to vw_model_labor_daily. `sources === null` means all sources;
// bound via @all_sources / UNNEST(@sources) — never interpolated.
export interface SalesBySourceRow {
  date: string;
  source: string | null;
  net_sales: number;
  orders: number;
  items_sold: number;
  avg_order_price: number;
  [key: string]: unknown;
}

export function salesByGrain(
  win: DateWindow,
  grain: Grain,
  sources: string[] | null,
  bySource: boolean,
): Promise<SalesBySourceRow[]> {
  const bucket = bucketSql(grain, "date_local");
  // bucketSql emits DATE_TRUNC(date_local, …) or the bare column for day grain.
  const sourceSelect = bySource ? "source" : "CAST(NULL AS STRING)";
  const groupBy = bySource ? "date, source" : "date";
  const allSources = sources == null;
  // None selected (Clear) — skip BQ; charts render empty until the operator
  // picks one or more sources.
  if (!allSources && sources.length === 0) {
    return Promise.resolve([]);
  }
  // Node BQ client cannot infer types for empty arrays ("Parameter types must
  // be provided for empty arrays…"). When @all_sources is true the UNNEST arm
  // is never evaluated, so a one-element sentinel is enough to bind STRING[].
  const sourcesParam = allSources ? ["__all__"] : sources;
  return q<SalesBySourceRow>(
    `WITH txn AS (
       SELECT
         date_local,
         source,
         SUM(net_sales_cents) AS net_sales_cents,
         COUNTIF(event_type = 'Payment') AS orders
       FROM ${fq("square_transactions")}
       WHERE date_local BETWEEN @start AND @end
         AND (@all_sources OR source IN UNNEST(@sources))
       GROUP BY date_local, source
     ),
     items AS (
       SELECT
         t.date_local,
         t.source,
         COUNT(*) AS items_sold
       FROM ${fq("square_item_lines")} i
       INNER JOIN ${fq("square_transactions")} t
         ON t.transaction_id = i.transaction_id
       WHERE t.date_local BETWEEN @start AND @end
         AND i.event_type = 'Payment'
         AND (@all_sources OR t.source IN UNNEST(@sources))
       GROUP BY t.date_local, t.source
     ),
     joined AS (
       SELECT
         COALESCE(txn.date_local, items.date_local) AS date_local,
         COALESCE(txn.source, items.source) AS source,
         COALESCE(txn.net_sales_cents, 0) AS net_sales_cents,
         COALESCE(txn.orders, 0) AS orders,
         COALESCE(items.items_sold, 0) AS items_sold
       FROM txn
       FULL OUTER JOIN items
         USING (date_local, source)
     )
     SELECT
       ${bucket} AS date,
       ${sourceSelect} AS source,
       SUM(net_sales_cents) / 100.0 AS net_sales,
       SUM(orders) AS orders,
       SUM(items_sold) AS items_sold,
       SAFE_DIVIDE(SUM(net_sales_cents) / 100.0, SUM(orders)) AS avg_order_price
     FROM joined
     GROUP BY ${groupBy}
     ORDER BY date DESC`,
    {
      start: dateParam(win.start),
      end: dateParam(win.end),
      all_sources: allSources,
      sources: sourcesParam,
    },
  );
}

export function salesSourceOptions(win: DateWindow): Promise<{ source: string }[]> {
  return q<{ source: string }>(
    `SELECT DISTINCT source
     FROM ${fq("square_transactions")}
     WHERE date_local BETWEEN @start AND @end
       AND source IS NOT NULL
       AND source != ''
     ORDER BY source`,
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

/**
 * Completed + projected (incl. scheduled) labor cost summary for a Period
 * window (Issue #166). Presentation math lives in
 * `lib/kpi/labor-forward.ts::computeLaborForwardSummary`; this query only
 * gathers the inputs (completed punches, ADP schedule, avg PT wage, AOV,
 * trailing FT $/day, optional `labor_burden_pct`).
 *
 * Completed = dates in [start, end] that are strictly before Chicago today.
 * Forward = dates in [start, end] on/after Chicago today from vw_model_forecast.
 */
export async function laborForwardSummary(
  win: DateWindow,
  store = "palmetto",
): Promise<LaborForwardSummary> {
  type Row = {
    completed_pt_cost: number;
    completed_ft_cost: number;
    completed_net_sales: number;
    completed_day_count: number;
    fwd_scheduled_hours: number;
    fwd_forecast_orders: number;
    fwd_days: number;
    avg_pt_wage: number | null;
    aov: number | null;
    avg_ft_cost_per_open_day: number | null;
    labor_burden_pct: number | null;
    fwd_pt_cost_from_employees: number | null;
  };
  const rows = await q<Row>(
    `WITH completed AS (
       SELECT
         COALESCE(SUM(hourly_labor_cost), 0) AS completed_pt_cost,
         COALESCE(SUM(fulltime_labor_cost), 0) AS completed_ft_cost,
         COALESCE(SUM(net_sales), 0) AS completed_net_sales,
         COUNT(*) AS completed_day_count
       FROM ${fq("vw_model_labor_daily")}
       WHERE date BETWEEN @start AND @end
         AND date < CURRENT_DATE('America/Chicago')
     ),
     fwd AS (
       SELECT
         COALESCE(SUM(scheduled_hours), 0) AS fwd_scheduled_hours,
         COALESCE(SUM(forecast_orders), 0) AS fwd_forecast_orders,
         COUNT(*) AS fwd_days
       FROM ${fq("vw_model_forecast")}
       WHERE date BETWEEN @start AND @end
         AND date >= CURRENT_DATE('America/Chicago')
         AND scheduled_hours IS NOT NULL
         AND scheduled_hours > 0
     ),
     fwd_emp AS (
       SELECT COALESCE(SUM(
         CASE
           WHEN IFNULL(w.is_salaried, FALSE) OR IFNULL(w.excluded_from_labor_pct, FALSE)
             THEN 0
           WHEN w.wage_rate_dollars IS NOT NULL
             THEN s.scheduled_hours * w.wage_rate_dollars
           ELSE s.scheduled_hours * (
             SELECT AVG(wage_rate_dollars) FROM ${fq("adp_wage_rates")}
             WHERE wage_rate_dollars IS NOT NULL
               AND NOT IFNULL(is_salaried, FALSE)
               AND NOT IFNULL(excluded_from_labor_pct, FALSE)
           )
         END
       ), 0) AS fwd_pt_cost_from_employees
       FROM ${fq("adp_scheduled_shifts")} s
       LEFT JOIN ${fq("adp_wage_rates")} w
         ON w.employee_id = s.employee_id
       WHERE s.date BETWEEN @start AND @end
         AND s.date >= CURRENT_DATE('America/Chicago')
         AND s.scheduled_hours > 0
     ),
     wage AS (
       SELECT AVG(wage_rate_dollars) AS avg_pt_wage
       FROM ${fq("adp_wage_rates")}
       WHERE wage_rate_dollars IS NOT NULL
         AND NOT IFNULL(is_salaried, FALSE)
         AND NOT IFNULL(excluded_from_labor_pct, FALSE)
     ),
     trail AS (
       SELECT
         SAFE_DIVIDE(SUM(net_sales), NULLIF(SUM(orders), 0)) AS aov,
         SAFE_DIVIDE(
           SUM(fulltime_labor_cost),
           NULLIF(COUNTIF(orders > 0 OR hourly_hours > 0 OR fulltime_hours > 0), 0)
         ) AS avg_ft_cost_per_open_day
       FROM ${fq("vw_model_labor_daily")}
       WHERE date BETWEEN DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 28 DAY)
         AND DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 1 DAY)
     ),
     burden AS (
       SELECT SAFE_CAST(value AS FLOAT64) AS labor_burden_pct
       FROM ${fq("store_config")}
       WHERE store = @store AND key = 'labor_burden_pct'
     )
     SELECT
       completed.completed_pt_cost,
       completed.completed_ft_cost,
       completed.completed_net_sales,
       completed.completed_day_count,
       fwd.fwd_scheduled_hours,
       fwd.fwd_forecast_orders,
       fwd.fwd_days,
       wage.avg_pt_wage,
       trail.aov,
       trail.avg_ft_cost_per_open_day,
       burden.labor_burden_pct,
       fwd_emp.fwd_pt_cost_from_employees
     FROM completed
     CROSS JOIN fwd
     CROSS JOIN fwd_emp
     CROSS JOIN wage
     CROSS JOIN trail
     LEFT JOIN burden ON TRUE`,
    { start: dateParam(win.start), end: dateParam(win.end), store },
  );
  const r = rows[0];
  if (!r) {
    return computeLaborForwardSummary({
      completedPtCost: 0,
      completedFtCost: 0,
      completedNetSales: 0,
      completedDayCount: 0,
      fwdScheduledHours: 0,
      fwdForecastOrders: 0,
      fwdDays: 0,
      avgPtWage: null,
      aov: null,
      avgFtCostPerOpenDay: null,
      laborBurdenPct: 0,
      fwdPtCostFromEmployees: null,
    });
  }
  return computeLaborForwardSummary({
    completedPtCost: Number(r.completed_pt_cost) || 0,
    completedFtCost: Number(r.completed_ft_cost) || 0,
    completedNetSales: Number(r.completed_net_sales) || 0,
    completedDayCount: Number(r.completed_day_count) || 0,
    fwdScheduledHours: Number(r.fwd_scheduled_hours) || 0,
    fwdForecastOrders: Number(r.fwd_forecast_orders) || 0,
    fwdDays: Number(r.fwd_days) || 0,
    avgPtWage: r.avg_pt_wage != null ? Number(r.avg_pt_wage) : null,
    aov: r.aov != null ? Number(r.aov) : null,
    avgFtCostPerOpenDay:
      r.avg_ft_cost_per_open_day != null ? Number(r.avg_ft_cost_per_open_day) : null,
    laborBurdenPct: r.labor_burden_pct != null ? Number(r.labor_burden_pct) : 0,
    fwdPtCostFromEmployees:
      r.fwd_pt_cost_from_employees != null ? Number(r.fwd_pt_cost_from_employees) : null,
  });
}

/** Per-employee scheduled hours in the Period (forward days with ADP shifts). */
export async function scheduledHoursPerPerson(
  win: DateWindow,
): Promise<{ employee: string; hours: number; cost: number | null }[]> {
  type Row = { employee: string; hours: number; cost: number | null };
  return q<Row>(
    `SELECT
       s.employee_name AS employee,
       SUM(s.scheduled_hours) AS hours,
       SUM(s.scheduled_hours * w.wage_rate_dollars) AS cost
     FROM ${fq("adp_scheduled_shifts")} s
     LEFT JOIN ${fq("adp_wage_rates")} w
       ON w.employee_id = s.employee_id
     WHERE s.date BETWEEN @start AND @end
       AND s.date >= CURRENT_DATE('America/Chicago')
       AND s.scheduled_hours > 0
     GROUP BY s.employee_name
     ORDER BY hours DESC`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

/** Daily projected PT labor % for forward scheduled days (chart dashed series). */
export async function laborProjectedByDay(
  win: DateWindow,
): Promise<{ date: string; projected_pt_pct: number | null }[]> {
  type Row = { date: string; projected_pt_pct: number | null };
  return q<Row>(
    `WITH day_cost AS (
       SELECT
         s.date,
         SUM(s.scheduled_hours * w.wage_rate_dollars) AS pt_cost
       FROM ${fq("adp_scheduled_shifts")} s
       INNER JOIN ${fq("adp_wage_rates")} w ON w.employee_id = s.employee_id
       WHERE s.date BETWEEN @start AND @end
         AND s.date >= CURRENT_DATE('America/Chicago')
         AND s.scheduled_hours > 0
         AND w.wage_rate_dollars IS NOT NULL
         AND NOT IFNULL(w.is_salaried, FALSE)
         AND NOT IFNULL(w.excluded_from_labor_pct, FALSE)
       GROUP BY s.date
     ),
     aov AS (
       SELECT SAFE_DIVIDE(SUM(net_sales), NULLIF(SUM(orders), 0)) AS aov
       FROM ${fq("vw_model_labor_daily")}
       WHERE date BETWEEN DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 28 DAY)
         AND DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 1 DAY)
     )
     SELECT
       CAST(f.date AS STRING) AS date,
       SAFE_DIVIDE(d.pt_cost, NULLIF(f.forecast_orders * aov.aov, 0)) AS projected_pt_pct
     FROM ${fq("vw_model_forecast")} f
     INNER JOIN day_cost d ON d.date = f.date
     CROSS JOIN aov
     WHERE f.date BETWEEN @start AND @end
       AND f.date >= CURRENT_DATE('America/Chicago')
       AND f.scheduled_hours IS NOT NULL AND f.scheduled_hours > 0
     ORDER BY f.date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
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

// Grain-aware forecast SELECT body (Issue #132 follow-on): sums volume
 // columns, then *recomputes* `orders_vs_prior_wk`/`items_vs_prior_wk` from
 // the summed totals — never averages daily ratios (same anti-pattern
 // `laborByGrain` avoids). `dow` is day-grain-only.
function forecastGrainSelectSql(grain: Grain): string {
  const bucket = bucketSql(grain);
  const dow = grain === "day" ? "ANY_VALUE(dow)" : "CAST(NULL AS STRING)";
  return `SELECT
       ${bucket} AS date,
       ${dow} AS dow,
       SUM(forecast_orders) AS forecast_orders,
       SUM(forecast_items) AS forecast_items,
       SUM(prior_wk_orders) AS prior_wk_orders,
       SUM(prior_wk_items) AS prior_wk_items,
       SAFE_DIVIDE(SUM(forecast_orders) - SUM(prior_wk_orders), NULLIF(SUM(prior_wk_orders), 0)) AS orders_vs_prior_wk,
       SAFE_DIVIDE(SUM(forecast_items) - SUM(prior_wk_items), NULLIF(SUM(prior_wk_items), 0)) AS items_vs_prior_wk,
       SUM(scheduled_hours) AS scheduled_hours
     FROM ${fq("vw_model_forecast")}`;
}

// Period-scoped forecast reader — kept for callers that intentionally clip
 // to a DateWindow. Prefer `forecastForwardByGrain` for the /forecast
 // upcoming-schedule / forward charts (Issue #202).
export function forecastByGrain(win: DateWindow, grain: Grain): Promise<ForecastRow[]> {
  return q<ForecastRow>(
    `${forecastGrainSelectSql(grain)}
     WHERE date BETWEEN @start AND @end
     GROUP BY date
     ORDER BY date`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

// Forward-looking forecast rows: Chicago today → pipeline horizon
 // (`forecast_horizon_days`, typically 30). Ignores the Performance Period
 // control so "This month" near month-end does not clip the upcoming
 // schedule / forward charts (Issue #202). `vw_model_forecast` already
 // filters `date >= CURRENT_DATE('America/Chicago')`; the explicit predicate
 // here documents the contract and stays correct if the view ever widens.
export function forecastForwardByGrain(grain: Grain): Promise<ForecastRow[]> {
  return q<ForecastRow>(
    `${forecastGrainSelectSql(grain)}
     WHERE date >= CURRENT_DATE('America/Chicago')
     GROUP BY date
     ORDER BY date`,
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

export interface ForecastGoalScheduleRow {
  date: string;
  forecast_items: number;
  scheduled_hours: number | null;
  [key: string]: unknown;
}

// Period-scoped goal/schedule inputs for the Goal vs Scheduled chart
 // (Issue #202). Reads the underlying tables — not `vw_model_forecast` —
 // because that view is forward-only (`date >= CURRENT_DATE`). Scheduled
 // hours act like "actuals" and must look back across the Period window.
export function forecastGoalScheduleByGrain(
  win: DateWindow,
  grain: Grain,
): Promise<ForecastGoalScheduleRow[]> {
  const bucket = bucketSql(grain, "d.date");
  return q<ForecastGoalScheduleRow>(
    `WITH days AS (
       SELECT date FROM ${fq("model_forecast_daily")}
       WHERE date BETWEEN @start AND @end
       UNION DISTINCT
       SELECT date FROM ${fq("adp_scheduled_daily")}
       WHERE date BETWEEN @start AND @end
     )
     SELECT
       ${bucket} AS date,
       SUM(f.forecast_items) AS forecast_items,
       SUM(s.scheduled_hours) AS scheduled_hours
     FROM days d
     LEFT JOIN ${fq("model_forecast_daily")} f ON f.date = d.date
     LEFT JOIN ${fq("adp_scheduled_daily")} s ON s.date = d.date
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
  /** Manual recognition bonus dollars (migration 049). */
  recognition_bonus: number;
  recognition_reason: string | null;
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

/** Issue #194 — day-grain usage include/exclude audit (last 30 CT days). */
export interface UsageDayAuditRow {
  store: string;
  item: string;
  submitted_date: string; // DATE as YYYY-MM-DD
  qty: number | null;
  delta: number | null;
  rule_eligible: boolean | null;
  in_avg: boolean | null;
  status: "included" | "excluded" | string;
  reason: string | null;
  override_mode: "force_include" | "force_exclude" | string | null;
  high_bar: number | null;
  similar_tomorrow_passes: boolean | null;
}

export function usageDayAudit(store = "palmetto"): Promise<UsageDayAuditRow[]> {
  return q<UsageDayAuditRow>(
    `SELECT store, item,
       CAST(submitted_date AS STRING) AS submitted_date,
       qty, delta, rule_eligible, in_avg, status, reason, override_mode,
       high_bar, similar_tomorrow_passes
     FROM ${fq("vw_inventory_usage_day_audit")}
     WHERE store = @store
     ORDER BY submitted_date DESC, item`,
    { store },
  );
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

/** Long-format reco rows for all live next-dates slots (migration 052). */
export interface OrderRecoSlotLongRow {
  Item: string;
  Slot: number;
  delivery_date: string;
  "Current Qty": number;
  "Avg per day": number;
  "On Hand at Restock": number | null;
  "Order Tubs": number | null;
  "Order Weight lbs": number | null;
  "After Restock": number | null;
  "Days Left After Restock": number | null;
  Source: "Estimated" | "Actuals" | null;
  _ord: number;
}

export function orderRecoSlots(): Promise<OrderRecoSlotLongRow[]> {
  return q<OrderRecoSlotLongRow>(
    `SELECT
       r.Item,
       r.Slot,
       CAST(r.delivery_date AS STRING) AS delivery_date,
       r.\`Current Qty\`,
       r.\`Avg per day\`,
       r.\`On Hand at Restock\`,
       r.\`Order Tubs\`,
       r.\`Order Weight lbs\`,
       r.\`After Restock\`,
       r.\`Days Left After Restock\`,
       IF(
         EXISTS (
           SELECT 1 FROM ${fq("inventory_restock_orders")} o
           WHERE o.store = 'palmetto' AND o.delivery_date = r.delivery_date
         ),
         'Actuals',
         'Estimated'
       ) AS Source,
       r._ord
     FROM ${fq("inventory_order_reco")} r
     INNER JOIN ${fq("vw_order_reco_next_dates")} d
       ON r.delivery_date = d.delivery_date
     WHERE r.store = 'palmetto'
     ORDER BY r._ord ASC, r.\`Current Qty\` DESC, r.Slot ASC`,
  );
}

// vw_order_reco_next_dates (031 + 041 + 051 + 052) — up to order_reco_max_slots
// planning dates (default 4): future, plus today until a base closing exists.
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
  // Same closing-aware today window as vw_order_reco_next_dates (migration 051):
  // keep today until a base closing for today exists (Current Qty absorbed restock).
  return q<EstimatedScheduleDateRow>(
    `SELECT s.delivery_date
     FROM ${fq("inventory_restock_schedule")} s
     LEFT JOIN (
       SELECT DISTINCT delivery_date
       FROM ${fq("inventory_restock_orders")}
       WHERE store = @store
     ) o ON s.delivery_date = o.delivery_date
     WHERE s.store = @store
       AND (
         s.delivery_date > CURRENT_DATE('America/Chicago')
         OR (
           s.delivery_date = CURRENT_DATE('America/Chicago')
           AND NOT EXISTS (
             SELECT 1
             FROM ${fq("inventory_closing_daily")} c
             WHERE c.store = @store
               AND c.category = 'base'
               AND c.submitted_date = CURRENT_DATE('America/Chicago')
           )
         )
       )
       AND o.delivery_date IS NULL
     ORDER BY s.delivery_date`,
    { store },
  );
}

// vw_inventory_base_runway (migration 036, Issue #164) — dual restock slots
// matching Next delivery; Actuals-only Status 1/2; Stockout 2 chains via D1.
export interface BaseRunwayRow {
  Base: string;
  Stock: number;
  "Vel per day": number;
  "Days left": number | null;
  "Stockout 1": string | null;
  "Restock 1": string | null;
  "Qty 1": number | null;
  "Status 1": "Risky" | "Fine";
  "Stockout 2": string | null;
  "Restock 2": string | null;
  "Qty 2": number | null;
  "Status 2": "Risky" | "Fine";
  [key: string]: unknown;
}

export function baseRunway(): Promise<BaseRunwayRow[]> {
  return q<BaseRunwayRow>(`SELECT * FROM ${fq("vw_inventory_base_runway")}`);
}

// training_shifts / tip exemptions (migration 020 + 038 windows — Issue #167).
export interface TrainingShiftRow {
  employee_name: string;
  date: string;
  exempt_start: string | null;
  exempt_end: string | null;
  note: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export function trainingShifts(store: string, days: number): Promise<TrainingShiftRow[]> {
  return q<TrainingShiftRow>(
    `SELECT employee_name, date, exempt_start, exempt_end, note, updated_by, updated_at
     FROM ${fq("training_shifts")}
     WHERE store=@store AND date >= DATE_SUB(CURRENT_DATE("America/Chicago"), INTERVAL @days DAY)
     ORDER BY date DESC`,
    { store, days },
  );
}

export interface AdpShiftRow {
  employee_name: string;
  date: string;
  in_time: string;
  out_time: string;
  total_hours: number;
}

/** Closed-out ADP shifts for a pay-period window (tip-exemption editor). */
export function adpShiftsForPeriod(
  store: string,
  start: string,
  end: string,
): Promise<AdpShiftRow[]> {
  return q<AdpShiftRow>(
    `SELECT canonical_name AS employee_name, CAST(date AS STRING) AS date,
            in_time, out_time, total_hours
     FROM ${fq("adp_shifts")}
     WHERE date BETWEEN @start AND @end
       AND canonical_name IS NOT NULL AND canonical_name != ""
     ORDER BY date, canonical_name`,
    { start: dateParam(start), end: dateParam(end) },
  );
}

export interface TipExemptionRow {
  employee_name: string;
  date: string;
  exempt_start: string | null;
  exempt_end: string | null;
  note: string | null;
  updated_by: string | null;
  updated_at: string | null;
  has_shift: boolean;
}

/** Tip exemptions for a period, with orphan flag when no ADP shift exists. */
export function tipExemptions(
  store: string,
  start: string,
  end: string,
): Promise<TipExemptionRow[]> {
  return q<TipExemptionRow>(
    `SELECT t.employee_name, CAST(t.date AS STRING) AS date,
            t.exempt_start, t.exempt_end, t.note, t.updated_by,
            CAST(t.updated_at AS STRING) AS updated_at,
            EXISTS(
              SELECT 1 FROM ${fq("adp_shifts")} s
              WHERE s.date = t.date AND s.canonical_name = t.employee_name
            ) AS has_shift
     FROM ${fq("training_shifts")} t
     WHERE t.store=@store AND t.date BETWEEN @start AND @end
     ORDER BY t.date DESC, t.employee_name`,
    { store, start: dateParam(start), end: dateParam(end) },
  );
}

/** Canonical employee names for orphan-exemption picker. */
export function listCanonicalEmployees(_store: string): Promise<{ employee_name: string }[]> {
  return q<{ employee_name: string }>(
    `SELECT DISTINCT canonical_name AS employee_name
     FROM ${fq("adp_shifts")}
     WHERE canonical_name IS NOT NULL AND canonical_name != ""
       AND date >= DATE_SUB(CURRENT_DATE("America/Chicago"), INTERVAL 90 DAY)
     ORDER BY canonical_name`,
  );
}

export interface PayPeriodOption {
  period_start: string;
  period_end: string;
  /** True when ADP has not paid tips for this biweek (adp_total_paid IS NULL). */
  unpaid: boolean;
  /** In-progress calendar biweek (after last closed end). */
  is_current: boolean;
}

/**
 * Pay periods for the Payroll dropdown (Issue #170).
 * Full biweeks from period_summary plus the in-progress calendar open window
 * (e.g. Jul 13–26) even when the model only has a 1-day stub.
 */
export async function listPayPeriodsWithPaidStatus(
  limit = 6,
): Promise<PayPeriodOption[]> {
  const {
    calendarOpenPayPeriod,
    isPeriodUnpaid,
  } = await import("@/lib/payroll/openPeriod");

  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const current = calendarOpenPayPeriod(today);

  const rows = await q<{
    period_start: string;
    period_end: string;
    adp_total_paid: number | null;
  }>(
    `SELECT CAST(period_start AS STRING) AS period_start,
            CAST(period_end AS STRING) AS period_end,
            adp_total_paid
     FROM ${fq("vw_model_period_summary")}
     WHERE DATE_DIFF(period_end, period_start, DAY) >= 13
     ORDER BY period_start DESC
     LIMIT @limit`,
    { limit: intParam(limit) },
  );

  const out: PayPeriodOption[] = [
    {
      period_start: current.start,
      period_end: current.end,
      unpaid: true,
      is_current: true,
    },
  ];
  for (const r of rows) {
    if (r.period_start === current.start) continue;
    out.push({
      period_start: r.period_start,
      period_end: r.period_end,
      unpaid: isPeriodUnpaid(r.adp_total_paid),
      is_current: false,
    });
  }
  return out;
}

/** All unpaid windows (current calendar + unpaid closed biweeks) for write guard. */
export async function unpaidPayPeriodWindows(): Promise<
  { start: string; end: string }[]
> {
  const periods = await listPayPeriodsWithPaidStatus(8);
  return periods.filter((p) => p.unpaid).map((p) => ({
    start: p.period_start,
    end: p.period_end,
  }));
}

/**
 * Primary unpaid bounds for default selection (just-ended unpaid biweek, else
 * calendar open). Write guard uses ``unpaidPayPeriodWindows`` (all unpaid).
 */
export async function openPayPeriodBounds(): Promise<{ start: string; end: string } | null> {
  const {
    mostRecentClosedPeriod,
    unpaidCurrentPayPeriod,
    isPeriodUnpaid,
  } = await import("@/lib/payroll/openPeriod");

  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Chicago",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());

  const closed = mostRecentClosedPeriod(today);
  const rows = await q<{ adp_total_paid: number | null }>(
    `SELECT adp_total_paid
     FROM ${fq("vw_model_period_summary")}
     WHERE period_start = @start AND period_end = @end
     LIMIT 1`,
    { start: dateParam(closed.start), end: dateParam(closed.end) },
  );
  const closedPaid = rows.length > 0 && !isPeriodUnpaid(rows[0].adp_total_paid);
  return unpaidCurrentPayPeriod(today, closedPaid);
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

// ── Plaid Accounting (Issue #158, migration 037) ──────────────────────────

export interface PlaidItemRow {
  store: string;
  item_id: string;
  institution_name: string | null;
  cursor: string | null;
  linked_at: string | null;
  linked_by: string | null;
  last_synced_at: string | null;
}

export function plaidItems(store: string): Promise<PlaidItemRow[]> {
  return q<PlaidItemRow>(
    `SELECT store, item_id, institution_name, cursor, linked_at, linked_by, last_synced_at
     FROM ${fq("plaid_items")} WHERE store=@store
     ORDER BY linked_at DESC`,
    { store },
  );
}

export interface PlaidAccountRow {
  account_id: string;
  mask: string | null;
  type: string | null;
}

export function plaidAccountsForItem(itemId: string): Promise<PlaidAccountRow[]> {
  return q<PlaidAccountRow>(
    `SELECT account_id, mask, type FROM ${fq("plaid_accounts")} WHERE item_id=@item_id`,
    { item_id: itemId },
  );
}

/** Rows needed to auto-flag checking↔own-card transfers after sync. */
export function plaidTxnHintsForItem(itemId: string): Promise<
  {
    transaction_id: string;
    account_id: string | null;
    name: string | null;
    merchant_name: string | null;
    amount: number;
    date: string;
    pfc_primary: string | null;
    pfc_detailed: string | null;
    is_internal: boolean | null;
  }[]
> {
  return q(
    `SELECT
       transaction_id, account_id, name, merchant_name, amount,
       CAST(date AS STRING) AS date, pfc_primary, pfc_detailed,
       IFNULL(is_internal, FALSE) AS is_internal
     FROM ${fq("plaid_transactions")}
     WHERE item_id=@item_id`,
    { item_id: itemId },
  );
}

export interface PlaidTransactionRow {
  transaction_id: string;
  date: string;
  name: string | null;
  merchant_name: string | null;
  amount: number;
  pending: boolean | null;
  pfc_primary: string | null;
  pfc_detailed: string | null;
  account_id: string | null;
  account_name: string | null;
  account_mask: string | null;
  account_type: string | null;
  payment_channel: string | null;
  counterparty_name: string | null;
  is_internal: boolean | null;
  category_id: string | null;
  subcategory_id: string | null;
  rule_id: string | null;
  override_category_id: string | null;
  override_subcategory_id: string | null;
  category_label: string | null;
  subcategory_label: string | null;
  category_definition: string | null;
  rule_pattern: string | null;
  rule_operator: string | null;
  rule_priority: number | null;
}

export function plaidTransactions(win: DateWindow): Promise<PlaidTransactionRow[]> {
  return q<PlaidTransactionRow>(
    `SELECT
       t.transaction_id,
       t.date,
       t.name,
       t.merchant_name,
       t.amount,
       t.pending,
       t.pfc_primary,
       t.pfc_detailed,
       t.account_id,
       a.name AS account_name,
       a.mask AS account_mask,
       a.type AS account_type,
       JSON_VALUE(t.raw_json, '$.payment_channel') AS payment_channel,
       JSON_VALUE(t.raw_json, '$.counterparties[0].name') AS counterparty_name,
       IFNULL(t.is_internal, FALSE) AS is_internal,
       t.category_id,
       t.subcategory_id,
       t.rule_id,
       t.override_category_id,
       t.override_subcategory_id,
       COALESCE(oc.label, c.label) AS category_label,
       COALESCE(os.label, s.label) AS subcategory_label,
       COALESCE(oc.definition, c.definition) AS category_definition,
       r.match_pattern AS rule_pattern,
       r.match_operator AS rule_operator,
       r.priority AS rule_priority
     FROM ${fq("plaid_transactions")} t
     LEFT JOIN ${fq("plaid_accounts")} a ON a.account_id = t.account_id
     LEFT JOIN ${fq("plaid_taxonomy_nodes")} c ON c.id = t.category_id
     LEFT JOIN ${fq("plaid_taxonomy_nodes")} s ON s.id = t.subcategory_id
     LEFT JOIN ${fq("plaid_taxonomy_nodes")} oc ON oc.id = t.override_category_id
     LEFT JOIN ${fq("plaid_taxonomy_nodes")} os ON os.id = t.override_subcategory_id
     LEFT JOIN ${fq("plaid_category_rules")} r ON r.id = t.rule_id
     WHERE t.date BETWEEN @start AND @end
     ORDER BY t.date DESC, t.transaction_id
     LIMIT 5000`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

export interface PlaidSpendCategoryRow {
  pfc_primary: string;
  category_label?: string;
  category_id?: string | null;
  category_slug?: string | null;
  spend: number;
  txn_count: number;
}

export function plaidSpendByCategory(win: DateWindow): Promise<PlaidSpendCategoryRow[]> {
  return q<PlaidSpendCategoryRow>(
    `SELECT
       COALESCE(category_label, pfc_primary, 'Uncategorized') AS pfc_primary,
       COALESCE(category_label, pfc_primary, 'Uncategorized') AS category_label,
       category_id,
       category_slug,
       SUM(spend) AS spend,
       SUM(txn_count) AS txn_count
     FROM ${fq("vw_plaid_spend_by_category_daily")}
     WHERE date BETWEEN @start AND @end
     GROUP BY 1, 2, 3, 4
     ORDER BY spend DESC`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  );
}

export function plaidMoneyInTotal(win: DateWindow): Promise<number> {
  return q<{ money_in: number }>(
    `SELECT COALESCE(SUM(money_in), 0) AS money_in
     FROM ${fq("vw_plaid_money_in_daily")}
     WHERE date BETWEEN @start AND @end`,
    { start: dateParam(win.start), end: dateParam(win.end) },
  ).then((rows) => Number(rows[0]?.money_in ?? 0));
}

export interface TaxonomyNodeRow {
  id: string;
  parent_id: string | null;
  slug: string;
  label: string;
  definition: string | null;
  enabled: boolean | null;
  sort_order: number | null;
  exclude_from_accounting: boolean | null;
}

export function plaidTaxonomyNodes(): Promise<TaxonomyNodeRow[]> {
  return q<TaxonomyNodeRow>(
    `SELECT id, parent_id, slug, label, definition, enabled, sort_order,
            exclude_from_accounting
     FROM ${fq("plaid_taxonomy_nodes")}
     WHERE IFNULL(enabled, TRUE) IS TRUE
     ORDER BY sort_order, label`,
  );
}

export interface CategoryRuleRow {
  id: string;
  priority: number;
  match_field: string;
  match_operator: string;
  match_pattern: string;
  amount_sign: string | null;
  category_id: string;
  subcategory_id: string | null;
  confidence: string | null;
  enabled: boolean | null;
  notes: string | null;
}

export function plaidCategoryRules(): Promise<CategoryRuleRow[]> {
  return q<CategoryRuleRow>(
    `SELECT id, priority, match_field, match_operator, match_pattern,
            amount_sign, category_id, subcategory_id, confidence, enabled, notes
     FROM ${fq("plaid_category_rules")}
     ORDER BY priority, id`,
  );
}
