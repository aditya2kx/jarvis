import "server-only";
import {
  laborDaily,
  storeConfig,
  orderQualityDaily,
  baseRunway,
  plaidSpendByCategory,
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

export type ScorecardGroupId = "finance" | "top_line" | "cost" | "quality" | "inventory";

export interface HealthMetric {
  key: string;
  label: string;
  actual: number | null;
  goal: number | null;
  status: GoalStatus;
  pace: number | null;
  formatted: string;
  goalFormatted: string;
  /** Null when the row is not editable (e.g. COGS not instrumented). */
  goalKey: GoalKey | null;
  rawGoal: string | undefined;
  info: string;
  /** Indent under a section header (Stripe / Linear Insights style). */
  nested?: boolean;
}

export interface HealthGroup {
  id: ScorecardGroupId;
  label: string;
  metrics: HealthMetric[];
}

function goalValue(config: StoreConfigRow[], key: string): number | null {
  const row = config.find((r) => r.key === key);
  return row ? Number(row.value) : null;
}

function goalRaw(config: StoreConfigRow[], key: string): string | undefined {
  return config.find((r) => r.key === key)?.value;
}

function periodGoal(
  config: StoreConfigRow[],
  win: DateWindow,
  weekly: GoalKey,
  monthly: GoalKey,
): { key: GoalKey; value: number | null; raw: string | undefined } {
  const key = isMonthLike(win.preset) ? monthly : weekly;
  return { key, value: goalValue(config, key), raw: goalRaw(config, key) };
}

export interface HealthScorecard {
  win: DateWindow;
  /** Flat list (tests / callers that don't need hierarchy). */
  metrics: HealthMetric[];
  /** Sectioned hierarchy for the Home UI. */
  groups: HealthGroup[];
  windowLabel: string;
}

function metric(partial: HealthMetric): HealthMetric {
  return { nested: true, ...partial };
}

export async function loadHealthScorecard(win: DateWindow): Promise<HealthScorecard> {
  const [rows, config, quality, runway, plaidCats] = await Promise.all([
    laborDaily(win),
    storeConfig(DEFAULT_STORE),
    orderQualityDaily(win),
    baseRunway(),
    plaidSpendByCategory(win).catch(() => []),
  ]);

  const netSales = sum(rows, (r) => r.net_sales);
  const laborCost = sum(rows, (r) => r.total_labor_cost);
  const orders = sum(rows, (r) => r.orders);
  const opsCost = plaidCats.reduce((s, c) => s + (c.spend ?? 0), 0);
  // Known costs only — COGS not instrumented (no silent fake).
  const totalKnownCost =
    laborCost == null && !plaidCats.length
      ? null
      : (laborCost ?? 0) + opsCost;
  const cashFlow =
    netSales == null && !plaidCats.length ? null : (netSales ?? 0) - opsCost;

  const prepP95 = avgPrepP95Min(quality);
  const riskyCount = countRiskyBases(runway);

  const gCash = periodGoal(config, win, "goal_cash_flow_weekly", "goal_cash_flow_monthly");
  const gSales = periodGoal(config, win, "goal_net_sales_weekly", "goal_net_sales_monthly");
  const gOrders = periodGoal(config, win, "goal_orders_weekly", "goal_orders_monthly");
  const gLabor$ = periodGoal(config, win, "goal_labor_cost_weekly", "goal_labor_cost_monthly");
  const gOps = periodGoal(config, win, "goal_ops_cost_weekly", "goal_ops_cost_monthly");
  const gTotal = periodGoal(config, win, "goal_total_cost_weekly", "goal_total_cost_monthly");
  const goalPrepP95 = goalValue(config, "goal_kds_p95_min");
  const goalRiskyMax = goalValue(config, "goal_bases_at_risk_max");

  const cashPace = paceFor(cashFlow, gCash.value, false);
  const salesPace = paceFor(netSales, gSales.value, false);
  const ordersPace = paceFor(orders, gOrders.value, false);
  const laborPace = paceFor(laborCost, gLabor$.value, true);
  const opsPace = paceFor(opsCost, gOps.value, true);
  const totalPace = paceFor(totalKnownCost, gTotal.value, true);
  const prepPace = paceFor(prepP95, goalPrepP95, true);
  const riskyPace = paceFor(riskyCount, goalRiskyMax, true);

  const finance: HealthMetric[] = [
    metric({
      key: "cash_flow",
      label: "Cash flow",
      actual: cashFlow,
      goal: gCash.value,
      status: statusFor(cashPace),
      pace: cashPace,
      formatted: fmtDollars(cashFlow),
      goalFormatted: fmtDollars(gCash.value),
      goalKey: gCash.key,
      rawGoal: gCash.raw,
      info: "Square net sales minus Plaid bank outflows for the period. Needs a linked bank for the spend side.",
    }),
  ];

  const topLine: HealthMetric[] = [
    metric({
      key: "net_sales",
      label: "Net sales",
      actual: netSales,
      goal: gSales.value,
      status: statusFor(salesPace),
      pace: salesPace,
      formatted: fmtDollars(netSales),
      goalFormatted: fmtDollars(gSales.value),
      goalKey: gSales.key,
      rawGoal: gSales.raw,
      info: isMonthLike(win.preset)
        ? "Total net sales vs goal_net_sales_monthly."
        : "Total net sales vs goal_net_sales_weekly.",
    }),
    metric({
      key: "orders",
      label: "# of orders",
      actual: orders,
      goal: gOrders.value,
      status: statusFor(ordersPace),
      pace: ordersPace,
      formatted: orders == null ? "—" : orders.toLocaleString("en-US"),
      goalFormatted: gOrders.value == null ? "—" : gOrders.value.toLocaleString("en-US"),
      goalKey: gOrders.key,
      rawGoal: gOrders.raw,
      info: "Sum of orders from vw_model_labor_daily over the selected period.",
    }),
  ];

  const cost: HealthMetric[] = [
    metric({
      key: "total_cost",
      label: "Total cost (known)",
      actual: totalKnownCost,
      goal: gTotal.value,
      status: statusFor(totalPace),
      pace: totalPace,
      formatted: fmtDollars(totalKnownCost),
      goalFormatted: fmtDollars(gTotal.value),
      goalKey: gTotal.key,
      rawGoal: gTotal.raw,
      info: "Labor $ + Plaid operations spend. Excludes COGS until food-cost / inventory COGS is instrumented.",
    }),
    metric({
      key: "labor_cost",
      label: "Labor cost",
      actual: laborCost,
      goal: gLabor$.value,
      status: statusFor(laborPace),
      pace: laborPace,
      formatted: fmtDollars(laborCost),
      goalFormatted: fmtDollars(gLabor$.value),
      goalKey: gLabor$.key,
      rawGoal: gLabor$.raw,
      info: "Total labor dollars (hourly + salaried) from vw_model_labor_daily.",
    }),
    metric({
      key: "cogs",
      label: "Cost of goods",
      actual: null,
      goal: null,
      status: "no-goal",
      pace: null,
      formatted: "—",
      goalFormatted: "—",
      goalKey: null,
      rawGoal: undefined,
      info: "Not instrumented yet — no silent placeholder. Follow-up when consumed COGS / food-cost lands.",
    }),
    metric({
      key: "ops_cost",
      label: "Operations / other",
      actual: opsCost,
      goal: gOps.value,
      status: statusFor(opsPace),
      pace: opsPace,
      formatted: fmtDollars(opsCost),
      goalFormatted: fmtDollars(gOps.value),
      goalKey: gOps.key,
      rawGoal: gOps.raw,
      info: "Plaid bank outflows (PFC rollup) for the period — interim stand-in for ops/other until custom taxonomy (#160).",
    }),
  ];

  const qualityMetrics: HealthMetric[] = [
    metric({
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
      info: "Mean daily KDS per-item p95 prep minutes (vw_order_quality_daily). Goal default 8.",
    }),
  ];

  const inventoryMetrics: HealthMetric[] = [
    metric({
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
      info: "Count of bases with Status=Risky on Inventory Base runway. Goal is usually 0.",
    }),
  ];

  const groups: HealthGroup[] = [
    { id: "finance", label: "Finance", metrics: finance },
    { id: "top_line", label: "Top line", metrics: topLine },
    { id: "cost", label: "Cost (bottom line)", metrics: cost },
    { id: "quality", label: "Quality", metrics: qualityMetrics },
    { id: "inventory", label: "Inventory", metrics: inventoryMetrics },
  ];

  return {
    win,
    metrics: groups.flatMap((g) => g.metrics),
    groups,
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

function fmtMinutes(n: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)} min`;
}
