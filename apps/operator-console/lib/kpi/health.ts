import "server-only";
import {
  laborDaily,
  storeConfig,
  orderQualityDaily,
  baseRunway,
  type LaborDailyRow,
  type StoreConfigRow,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { isMonthLike, type DateWindow } from "@/lib/filters/range";
import type { GoalKey } from "@/lib/bq/writes";
import type { GoalStatus } from "@/lib/kpi/health-types";
import { avgPrepP95Min, countRiskyBases, paceFor, statusFor } from "@/lib/kpi/scorecard-math";

export type { GoalStatus };
export { avgPrepP95Min, countRiskyBases, paceFor, statusFor };

export interface HealthMetric {
  key: string;
  label: string;
  actual: number | null;
  goal: number | null;
  status: GoalStatus;
  pace: number | null;
  formatted: string;
  goalFormatted: string;
  /** `store_config` key an inline edit on this metric writes — see
   *  components/kpi/HealthScorecard.tsx's pencil-edit + goal-fields.ts. */
  goalKey: GoalKey;
  /** Raw `store_config` string value (not the parsed `goal` number) — the
   *  inline edit needs this to prefill in the field's own input units
   *  (e.g. GoalsDrawer/HealthScorecard convert a percent fraction to a
   *  whole-percent display string from this, not from `goal`). */
  rawGoal: string | undefined;
  /** One-line explanation shown behind the metric's info tooltip. */
  info: string;
}

function goalValue(config: StoreConfigRow[], key: string): number | null {
  const row = config.find((r) => r.key === key);
  return row ? Number(row.value) : null;
}

function goalRaw(config: StoreConfigRow[], key: string): string | undefined {
  return config.find((r) => r.key === key)?.value;
}

export interface HealthScorecard {
  win: DateWindow;
  metrics: HealthMetric[];
  windowLabel: string;
}

export async function loadHealthScorecard(win: DateWindow): Promise<HealthScorecard> {
  const [rows, config, quality, runway] = await Promise.all([
    laborDaily(win),
    storeConfig(DEFAULT_STORE),
    orderQualityDaily(win),
    baseRunway(),
  ]);

  const netSales = sum(rows, (r) => r.net_sales);
  const laborCost = sum(rows, (r) => r.total_labor_cost);
  const hourlyLaborCost = sum(rows, (r) => Number(r.hourly_labor_cost ?? 0));
  const laborPct = netSales && laborCost != null ? laborCost / netSales : null;
  const hourlyLaborPct =
    netSales && hourlyLaborCost != null ? hourlyLaborCost / netSales : null;

  const prepP95 = avgPrepP95Min(quality);
  const riskyCount = countRiskyBases(runway);

  const netSalesGoalKey: GoalKey = isMonthLike(win.preset)
    ? "goal_net_sales_monthly"
    : "goal_net_sales_weekly";
  const goalNetSales = goalValue(config, netSalesGoalKey);
  const goalHourlyLaborMax = goalValue(config, "goal_hourly_labor_pct_max");
  const goalLaborPctMax = goalValue(config, "goal_labor_pct_max");
  const goalPrepP95 = goalValue(config, "goal_kds_p95_min");
  const goalRiskyMax = goalValue(config, "goal_bases_at_risk_max");

  const netSalesPace = paceFor(netSales, goalNetSales, false);
  const hourlyLaborPace = paceFor(hourlyLaborPct, goalHourlyLaborMax, true);
  const laborPctPace = paceFor(laborPct, goalLaborPctMax, true);
  const prepPace = paceFor(prepP95, goalPrepP95, true);
  const riskyPace = paceFor(riskyCount, goalRiskyMax, true);

  const metrics: HealthMetric[] = [
    {
      key: "net_sales",
      label: "Net sales",
      actual: netSales,
      goal: goalNetSales,
      status: statusFor(netSalesPace),
      pace: netSalesPace,
      formatted: fmtDollars(netSales),
      goalFormatted: fmtDollars(goalNetSales),
      goalKey: netSalesGoalKey,
      rawGoal: goalRaw(config, netSalesGoalKey),
      info: isMonthLike(win.preset)
        ? "Total net sales for the selected period vs the monthly target (goal_net_sales_monthly)."
        : "Total net sales for the selected period vs the weekly target (goal_net_sales_weekly).",
    },
    {
      key: "hourly_labor_pct",
      label: "Labor % — part-time",
      actual: hourlyLaborPct,
      goal: goalHourlyLaborMax,
      status: statusFor(hourlyLaborPace),
      pace: hourlyLaborPace,
      formatted: fmtPct(hourlyLaborPct),
      goalFormatted: fmtPct(goalHourlyLaborMax),
      goalKey: "goal_hourly_labor_pct_max",
      rawGoal: goalRaw(config, "goal_hourly_labor_pct_max"),
      info: "Hourly / part-time labor cost as a % of net sales — same breakout as the Labor page PT series.",
    },
    {
      key: "labor_pct",
      label: "Labor % — total",
      actual: laborPct,
      goal: goalLaborPctMax,
      status: statusFor(laborPctPace),
      pace: laborPctPace,
      formatted: fmtPct(laborPct),
      goalFormatted: fmtPct(goalLaborPctMax),
      goalKey: "goal_labor_pct_max",
      rawGoal: goalRaw(config, "goal_labor_pct_max"),
      info: "Total labor (hourly staff + salaried/manager) as a % of net sales.",
    },
    {
      key: "prep_p95_min",
      label: "Prep time p95",
      actual: prepP95,
      goal: goalPrepP95,
      status: statusFor(prepPace),
      pace: prepPace,
      formatted: fmtMinutes(prepP95),
      goalFormatted: fmtMinutes(goalPrepP95),
      goalKey: "goal_kds_p95_min",
      rawGoal: goalRaw(config, "goal_kds_p95_min"),
      info: "Mean daily KDS per-item p95 prep minutes over the period (vw_order_quality_daily.kds_p95_min). Goal default 8.",
    },
    {
      key: "bases_at_risk",
      label: "Bases at risk",
      actual: riskyCount,
      goal: goalRiskyMax,
      status: statusFor(riskyPace),
      pace: riskyPace,
      formatted: String(riskyCount),
      goalFormatted: goalRiskyMax == null ? "—" : String(goalRiskyMax),
      goalKey: "goal_bases_at_risk_max",
      rawGoal: goalRaw(config, "goal_bases_at_risk_max"),
      info: "Count of bases with Status=Risky on Inventory Base runway (stockout before next Actuals restock). Goal is usually 0.",
    },
  ];

  return {
    win,
    metrics,
    windowLabel: win.label,
  };
}

function sum(rows: LaborDailyRow[], pick: (r: LaborDailyRow) => number | null | undefined): number | null {
  if (!rows.length) return null;
  return rows.reduce((s, r) => s + (pick(r) ?? 0), 0);
}

function fmtDollars(n: number | null): string {
  return n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function fmtPct(n: number | null): string {
  return n == null ? "—" : `${(n * 100).toFixed(1)}%`;
}

function fmtMinutes(n: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)} min`;
}
