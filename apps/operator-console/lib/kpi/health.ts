import "server-only";
import {
  laborDaily,
  laborForwardSummary,
  storeConfig,
  orderQualityDaily,
  baseRunway,
  plaidSpendByCategory,
  type LaborDailyRow,
  type StoreConfigRow,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { chicagoTodayIso, isMonthLike, type DateWindow } from "@/lib/filters/range";
import type { GoalKey } from "@/lib/bq/writes";
import type { GoalStatus } from "@/lib/kpi/health-types";
import { viewForLaborLens, type LaborLens } from "@/lib/kpi/labor-lens";
import {
  avgPrepP95Min,
  countRiskyBases,
  elapsedDaysInWindow,
  paceFor,
  rollupStatus,
  statusFor,
} from "@/lib/kpi/scorecard-math";

export type { GoalStatus };
export { avgPrepP95Min, countRiskyBases, elapsedDaysInWindow, paceFor, rollupStatus, statusFor };

export type ScorecardGroupId = "finance" | "top_line" | "cost" | "labor" | "quality" | "inventory";

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
  /** lower-is-better for GoalBar marker math. */
  lowerIsBetter?: boolean;
  /** Human “how far off” string, e.g. "$1.2k under (8%)". */
  deltaFormatted?: string;
}

export interface HealthGroup {
  id: ScorecardGroupId;
  label: string;
  /** Detail page for this section (left-nav destination). */
  href: string;
  metrics: HealthMetric[];
}

function deltaLabel(
  actual: number | null,
  goal: number | null,
  lowerIsBetter: boolean,
  kind: "dollars" | "number" | "minutes",
): string | undefined {
  if (actual == null || goal == null) return undefined;
  const diff = actual - goal;
  if (diff === 0) return "on goal";
  const good = lowerIsBetter ? diff <= 0 : diff >= 0;
  const abs = Math.abs(diff);
  const pct = goal !== 0 ? Math.abs(diff / goal) * 100 : null;
  let mag: string;
  if (kind === "dollars") {
    mag = abs.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  } else if (kind === "minutes") {
    mag = `${abs.toFixed(1)} min`;
  } else {
    mag = abs.toFixed(1);
  }
  const pctPart = pct != null && Number.isFinite(pct) ? ` (${pct.toFixed(0)}%)` : "";
  if (good) return lowerIsBetter ? `${mag} under${pctPart}` : `${mag} over${pctPart}`;
  return lowerIsBetter ? `${mag} over${pctPart}` : `${mag} under${pctPart}`;
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
  /** Worst-wins rollup across all scored metrics. */
  overallStatus: GoalStatus;
}

function metric(partial: HealthMetric): HealthMetric {
  return { nested: true, ...partial };
}

export async function loadHealthScorecard(
  win: DateWindow,
  opts: { laborLens?: LaborLens } = {},
): Promise<HealthScorecard> {
  const laborLens: LaborLens = opts.laborLens ?? "wage";
  const [rows, config, quality, runway, plaidCats, laborFwd] = await Promise.all([
    laborDaily(win),
    storeConfig(DEFAULT_STORE),
    orderQualityDaily(win),
    baseRunway(),
    plaidSpendByCategory(win).catch(() => []),
    laborForwardSummary(win, DEFAULT_STORE).catch(() => null),
  ]);

  const netSales = sum(rows, (r) => r.net_sales);
  const laborCost = sum(rows, (r) => r.total_labor_cost);
  const ordersTotal = sum(rows, (r) => r.orders);
  // Cap at Chicago today so this_month does not dilute avg by future days.
  const dayCount = elapsedDaysInWindow(win.start, win.end, chicagoTodayIso());
  const ordersPerDay =
    ordersTotal == null ? null : ordersTotal / dayCount;
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
  const goalOrdersPerDay = goalValue(config, "goal_orders_per_day");
  const gLabor$ = periodGoal(config, win, "goal_labor_cost_weekly", "goal_labor_cost_monthly");
  const gOps = periodGoal(config, win, "goal_ops_cost_weekly", "goal_ops_cost_monthly");
  const gTotal = periodGoal(config, win, "goal_total_cost_weekly", "goal_total_cost_monthly");
  const goalPtLaborPct = goalValue(config, "goal_hourly_labor_pct_max");
  const goalTotalLaborPct = goalValue(config, "goal_labor_pct_max");
  const goalPrepP95 = goalValue(config, "goal_kds_p95_min");
  const goalRiskyMax = goalValue(config, "goal_bases_at_risk_max");

  const cashPace = paceFor(cashFlow, gCash.value, false);
  const salesPace = paceFor(netSales, gSales.value, false);
  const ordersPace = paceFor(ordersPerDay, goalOrdersPerDay, false);
  const laborPace = paceFor(laborCost, gLabor$.value, true);
  const opsPace = paceFor(opsCost, gOps.value, true);
  const totalPace = paceFor(totalKnownCost, gTotal.value, true);
  const lensView =
    laborFwd != null
      ? viewForLaborLens(laborFwd, laborLens)
      : null;
  const lensPtPct = lensView?.paidUnavailable ? null : (lensView?.ptPct ?? null);
  const lensTotalPct = lensView?.paidUnavailable ? null : (lensView?.totalPct ?? null);
  const ptLaborPace = paceFor(lensPtPct, goalPtLaborPct, true);
  const totalLaborPctPace = paceFor(lensTotalPct, goalTotalLaborPct, true);
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
      lowerIsBetter: false,
      deltaFormatted: deltaLabel(cashFlow, gCash.value, false, "dollars"),
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
      lowerIsBetter: false,
      deltaFormatted: deltaLabel(netSales, gSales.value, false, "dollars"),
      info: isMonthLike(win.preset)
        ? "Total net sales vs goal_net_sales_monthly."
        : "Total net sales vs goal_net_sales_weekly.",
    }),
    metric({
      key: "orders",
      label: "Avg orders / day",
      actual: ordersPerDay,
      goal: goalOrdersPerDay,
      status: statusFor(ordersPace),
      pace: ordersPace,
      formatted: ordersPerDay == null ? "—" : ordersPerDay.toFixed(1),
      goalFormatted: goalOrdersPerDay == null ? "—" : String(goalOrdersPerDay),
      goalKey: "goal_orders_per_day",
      rawGoal: goalRaw(config, "goal_orders_per_day"),
      lowerIsBetter: false,
      deltaFormatted: deltaLabel(ordersPerDay, goalOrdersPerDay, false, "number"),
      info: `Period order count ÷ ${dayCount} elapsed days through today (America/Chicago), not full calendar month.`,
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
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(totalKnownCost, gTotal.value, true, "dollars"),
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
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(laborCost, gLabor$.value, true, "dollars"),
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
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(opsCost, gOps.value, true, "dollars"),
      info: "Plaid bank outflows (PFC rollup) for the period — interim stand-in for ops/other until custom taxonomy (#160).",
    }),
  ];

  const laborInfo =
    lensView?.description ??
    "Labor % unavailable — check vw_model_labor_daily / ADP schedule ingest.";
  const laborMetrics: HealthMetric[] = [
    metric({
      key: `pt_labor_pct_${laborLens}`,
      label: `Part-time — ${lensView?.title ?? "Wage"}`,
      actual: lensPtPct,
      goal: goalPtLaborPct,
      status: lensPtPct != null ? statusFor(ptLaborPace) : "no-goal",
      pace: lensPtPct != null ? ptLaborPace : null,
      formatted: fmtPct(lensPtPct),
      goalFormatted: fmtPct(goalPtLaborPct),
      goalKey: laborLens === "wage" ? "goal_hourly_labor_pct_max" : null,
      rawGoal: laborLens === "wage" ? goalRaw(config, "goal_hourly_labor_pct_max") : undefined,
      lowerIsBetter: true,
      deltaFormatted: lensPtPct != null ? deltaLabelPct(lensPtPct, goalPtLaborPct) : undefined,
      info: laborInfo,
    }),
    metric({
      key: `total_labor_pct_${laborLens}`,
      label: `Total (PT + FT) — ${lensView?.title ?? "Wage"}`,
      actual: lensTotalPct,
      goal: goalTotalLaborPct,
      status: lensTotalPct != null ? statusFor(totalLaborPctPace) : "no-goal",
      pace: lensTotalPct != null ? totalLaborPctPace : null,
      formatted: fmtPct(lensTotalPct),
      goalFormatted: fmtPct(goalTotalLaborPct),
      goalKey: laborLens === "wage" ? "goal_labor_pct_max" : null,
      rawGoal: laborLens === "wage" ? goalRaw(config, "goal_labor_pct_max") : undefined,
      lowerIsBetter: true,
      deltaFormatted:
        lensTotalPct != null ? deltaLabelPct(lensTotalPct, goalTotalLaborPct) : undefined,
      info: laborInfo,
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
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(prepP95, goalPrepP95, true, "minutes"),
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
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(riskyCount, goalRiskyMax, true, "number"),
      info: "Count of bases with Status=Risky on Inventory Base runway. Goal is usually 0.",
    }),
  ];

  const groups: HealthGroup[] = [
    { id: "finance", label: "Finance", href: "/accounting", metrics: finance },
    { id: "top_line", label: "Top line", href: "/sales", metrics: topLine },
    { id: "cost", label: "Cost (bottom line)", href: "/labor", metrics: cost },
    { id: "labor", label: "Labor", href: "/labor", metrics: laborMetrics },
    { id: "quality", label: "Quality", href: "/order-quality", metrics: qualityMetrics },
    { id: "inventory", label: "Inventory", href: "/inventory", metrics: inventoryMetrics },
  ];

  const metrics = groups.flatMap((g) => g.metrics);
  return {
    win,
    metrics,
    groups,
    windowLabel: win.label,
    overallStatus: rollupStatus(metrics.filter((m) => m.key !== "cogs").map((m) => m.status)),
  };
}

function sum(rows: LaborDailyRow[], pick: (r: LaborDailyRow) => number | null | undefined): number | null {
  if (!rows.length) return null;
  return rows.reduce((s, r) => s + (pick(r) ?? 0), 0);
}

function fmtDollars(n: number | null): string {
  return n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

/** Fraction 0–1 → whole-percent display (matches GoalsDrawer percent fields). */
function fmtPct(n: number | null): string {
  return n == null || !Number.isFinite(n) ? "—" : `${(n * 100).toFixed(1)}%`;
}

function deltaLabelPct(actual: number | null, goal: number | null): string | undefined {
  if (actual == null || goal == null) return undefined;
  const diffPp = (actual - goal) * 100;
  if (diffPp === 0) return "on goal";
  const mag = `${Math.abs(diffPp).toFixed(1)} pp`;
  return diffPp <= 0 ? `${mag} under` : `${mag} over`;
}

function fmtMinutes(n: number | null): string {
  return n == null ? "—" : `${n.toFixed(1)} min`;
}
