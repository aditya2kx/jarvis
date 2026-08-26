import "server-only";
import {
  laborDaily,
  storeConfig,
  orderQualityDaily,
  baseRunway,
  plaidSpendByCategory,
  plaidMoneyInTotal,
  type LaborDailyRow,
  type StoreConfigRow,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { chicagoTodayIso, isMonthLike, type DateWindow } from "@/lib/filters/range";
import type { GoalKey } from "@/lib/bq/writes";
import type { GoalStatus } from "@/lib/kpi/health-types";
import type { LaborLens } from "@/lib/kpi/labor-lens";
import { PAYROLL_LABOR_CATEGORY_ID } from "@/lib/plaid/exclude-accounting";
import {
  avgPrepP95Min,
  countRiskyBases,
  elapsedDaysInWindow,
  paceFor,
  rollupStatus,
  statusFor,
  weeklyHoursGoalForWindow,
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
  kind: "dollars" | "number" | "minutes" | "hours",
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
  } else if (kind === "hours") {
    mag = `${abs.toFixed(1)} hrs`;
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
  _opts: { laborLens?: LaborLens } = {},
): Promise<HealthScorecard> {
  // laborLens retained for API compat; Home no longer shows schedule/blended (#189).
  const [rows, config, quality, runway, plaidCats, moneyIn] = await Promise.all([
    laborDaily(win),
    storeConfig(DEFAULT_STORE),
    orderQualityDaily(win),
    baseRunway(),
    plaidSpendByCategory(win).catch(() => []),
    plaidMoneyInTotal(win).catch(() => 0),
  ]);

  const netSales = sum(rows, (r) => r.net_sales);
  const ptLabor$ = sum(rows, (r) => Number(r.hourly_labor_cost ?? 0));
  const ftLabor$ = sum(rows, (r) => Number(r.fulltime_labor_cost ?? 0));
  const totalRatesLabor$ = sum(rows, (r) => r.total_labor_cost);
  const laborHours = sum(rows, (r) => Number(r.total_hours ?? 0));
  const ordersTotal = sum(rows, (r) => r.orders);
  const dayCount = elapsedDaysInWindow(win.start, win.end, chicagoTodayIso());
  const ordersPerDay =
    ordersTotal == null ? null : ordersTotal / dayCount;

  const moneyOut = plaidCats.reduce((s, c) => s + (c.spend ?? 0), 0);
  const bankCashFlow =
    !plaidCats.length && moneyIn === 0 ? null : moneyIn - moneyOut;
  const totalBankCost = plaidCats.length ? moneyOut : null;

  const payrollCat = plaidCats.find(
    (c) =>
      c.category_id === PAYROLL_LABOR_CATEGORY_ID ||
      c.category_slug === PAYROLL_LABOR_CATEGORY_ID ||
      (c.category_label || "").toLowerCase().includes("payroll"),
  );
  const bankLabor$ = payrollCat?.spend ?? 0;

  const prepP95 = avgPrepP95Min(quality);
  const riskyCount = countRiskyBases(runway);

  const gCash = periodGoal(config, win, "goal_cash_flow_weekly", "goal_cash_flow_monthly");
  const gSales = periodGoal(config, win, "goal_net_sales_weekly", "goal_net_sales_monthly");
  const goalOrdersPerDay = goalValue(config, "goal_orders_per_day");
  const gLabor$ = periodGoal(config, win, "goal_labor_cost_weekly", "goal_labor_cost_monthly");
  const gTotal = periodGoal(config, win, "goal_total_cost_weekly", "goal_total_cost_monthly");
  const goalPtLaborPct = goalValue(config, "goal_hourly_labor_pct_max");
  const goalTotalLaborPct = goalValue(config, "goal_labor_pct_max");
  const goalPrepP95 = goalValue(config, "goal_kds_p95_min");
  const goalRiskyMax = goalValue(config, "goal_bases_at_risk_max");
  const goalHoursWeek = goalValue(config, "goal_labor_hours_week");
  const hoursGoal = weeklyHoursGoalForWindow(goalHoursWeek, dayCount);

  const pctOfSales = (n: number | null): number | null => {
    if (n == null || netSales == null || !netSales) return null;
    return n / netSales;
  };

  const cashPace = paceFor(bankCashFlow, gCash.value, false);
  const salesPace = paceFor(netSales, gSales.value, false);
  const ordersPace = paceFor(ordersPerDay, goalOrdersPerDay, false);
  const totalPace = paceFor(totalBankCost, gTotal.value, true);
  const prepPace = paceFor(prepP95, goalPrepP95, true);
  const riskyPace = paceFor(riskyCount, goalRiskyMax, true);
  const hoursPace = paceFor(laborHours, hoursGoal, true);

  const finance: HealthMetric[] = [
    metric({
      key: "money_in_bank",
      label: "Money in (bank)",
      actual: moneyIn,
      goal: null,
      status: "no-goal",
      pace: null,
      formatted: fmtDollars(moneyIn),
      goalFormatted: "—",
      goalKey: null,
      rawGoal: undefined,
      lowerIsBetter: false,
      info: "Plaid bank inflows for the period (excludes categories marked exclude-from-accounting). Matches Accounting money in.",
    }),
    metric({
      key: "money_out_bank",
      label: "Money out (bank)",
      actual: moneyOut,
      goal: null,
      status: "no-goal",
      pace: null,
      formatted: fmtDollars(moneyOut),
      goalFormatted: "—",
      goalKey: null,
      rawGoal: undefined,
      lowerIsBetter: true,
      info: "Plaid business outflows (exclude-from-accounting applied). Matches Accounting money out.",
    }),
    metric({
      key: "cash_flow",
      label: "Cash flow (bank)",
      actual: bankCashFlow,
      goal: gCash.value,
      status: statusFor(cashPace),
      pace: cashPace,
      formatted: fmtDollars(bankCashFlow),
      goalFormatted: fmtDollars(gCash.value),
      goalKey: gCash.key,
      rawGoal: gCash.raw,
      lowerIsBetter: false,
      deltaFormatted: deltaLabel(bankCashFlow, gCash.value, false, "dollars"),
      info: "Bank money in − business money out. Same definition as Accounting cash flow.",
    }),
  ];

  const topLine: HealthMetric[] = [
    metric({
      key: "net_sales",
      label: "Net sales (Square)",
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
        ? "Square POS net sales vs goal_net_sales_monthly."
        : "Square POS net sales vs goal_net_sales_weekly.",
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
      label: "Total cost (bank)",
      actual: totalBankCost,
      goal: gTotal.value,
      status: statusFor(totalPace),
      pace: totalPace,
      formatted: fmtDollars(totalBankCost),
      goalFormatted: fmtDollars(gTotal.value),
      goalKey: gTotal.key,
      rawGoal: gTotal.raw,
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(totalBankCost, gTotal.value, true, "dollars"),
      info: "Sum of business bank outflows by taxonomy parent (same as Accounting). % of Square net sales shown on parent rows.",
    }),
    ...plaidCats
      .filter((c) => (c.spend ?? 0) > 0)
      .map((c) => {
        const label = c.category_label || c.pfc_primary || "Uncategorized";
        const spend = c.spend ?? 0;
        const pct = pctOfSales(spend);
        const slug = c.category_slug || c.category_id || label;
        return metric({
          key: `cost_cat_${slug}`,
          label: `${label} · ${fmtPct(pct)} of sales`,
          actual: spend,
          goal: null,
          status: "no-goal",
          pace: null,
          formatted: fmtDollars(spend),
          goalFormatted: "—",
          goalKey: null,
          rawGoal: undefined,
          lowerIsBetter: true,
          info: `Bank spend in taxonomy parent "${label}". Matches Accounting filter for this category.`,
        });
      }),
  ];

  const ptPct = pctOfSales(ptLabor$);
  const ftPct = pctOfSales(ftLabor$);
  const totalRatesPct = pctOfSales(totalRatesLabor$);
  const bankLaborPct = pctOfSales(bankLabor$);
  const ptPace = paceFor(ptPct, goalPtLaborPct, true);
  const totalRatesPace = paceFor(totalRatesPct, goalTotalLaborPct, true);
  const bankLaborPace = paceFor(bankLabor$, gLabor$.value, true);

  const laborMetrics: HealthMetric[] = [
    metric({
      key: "labor_hours_week",
      label: "Labor hours",
      actual: laborHours,
      goal: hoursGoal,
      status: hoursGoal != null ? statusFor(hoursPace) : "no-goal",
      pace: hoursGoal != null ? hoursPace : null,
      formatted: fmtHours(laborHours),
      goalFormatted: fmtHours(hoursGoal),
      goalKey: "goal_labor_hours_week",
      rawGoal: goalRaw(config, "goal_labor_hours_week"),
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(laborHours, hoursGoal, true, "hours"),
      info: "ADP clocked hours in this Period vs the weekly hours max, scaled by days ÷ 7. Pencil edits the same store_config key as Labor → Hours goal.",
    }),
    metric({
      key: "labor_pt_rates",
      label: `Part-time (rates) · ${fmtPct(ptPct)}`,
      actual: ptLabor$,
      goal: null,
      status: ptPct != null ? statusFor(ptPace) : "no-goal",
      pace: ptPct != null ? ptPace : null,
      formatted: fmtDollars(ptLabor$),
      goalFormatted: "—",
      goalKey: "goal_hourly_labor_pct_max",
      rawGoal: goalRaw(config, "goal_hourly_labor_pct_max"),
      lowerIsBetter: true,
      deltaFormatted: ptPct != null ? deltaLabelPct(ptPct, goalPtLaborPct) : undefined,
      info: "Hourly (part-time) labor $ from current ADP wage_rates × clocked hours (not frozen model_labor_daily dollars).",
    }),
    metric({
      key: "labor_ft_rates",
      label: `Full-time (rates) · ${fmtPct(ftPct)}`,
      actual: ftLabor$,
      goal: null,
      status: "no-goal",
      pace: null,
      formatted: fmtDollars(ftLabor$),
      goalFormatted: "—",
      goalKey: null,
      rawGoal: undefined,
      lowerIsBetter: true,
      info: "Full-time / salaried / excluded-from-% labor $ from current ADP wage_rates × clocked hours.",
    }),
    metric({
      key: "labor_total_rates",
      label: `Total rates (PT+FT) · ${fmtPct(totalRatesPct)}`,
      actual: totalRatesLabor$,
      goal: gLabor$.value,
      status: totalRatesPct != null ? statusFor(totalRatesPace) : "no-goal",
      pace: totalRatesPct != null ? totalRatesPace : null,
      formatted: fmtDollars(totalRatesLabor$),
      goalFormatted: fmtDollars(gLabor$.value),
      goalKey: gLabor$.key,
      rawGoal: gLabor$.raw,
      lowerIsBetter: true,
      deltaFormatted: deltaLabel(totalRatesLabor$, gLabor$.value, true, "dollars"),
      info: "PT + FT wage cost from ADP rates. Not the same as bank payroll cash.",
    }),
    metric({
      key: "labor_bank",
      label: `Labor (bank) · ${fmtPct(bankLaborPct)}`,
      actual: bankLabor$,
      goal: null,
      status: statusFor(bankLaborPace),
      pace: bankLaborPace,
      formatted: fmtDollars(bankLabor$),
      goalFormatted: "—",
      goalKey: null,
      rawGoal: undefined,
      lowerIsBetter: true,
      info: "Bank payroll category spend — matches Cost / Accounting payroll parent for this period.",
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
    { id: "finance", label: "Finance (bank)", href: "/accounting", metrics: finance },
    { id: "top_line", label: "Sales (Square)", href: "/sales", metrics: topLine },
    { id: "cost", label: "Cost (bank)", href: "/accounting", metrics: cost },
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
    overallStatus: rollupStatus(
      metrics.filter((m) => m.status !== "no-goal").map((m) => m.status),
    ),
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

function fmtHours(n: number | null): string {
  return n == null || !Number.isFinite(n) ? "—" : `${n.toFixed(1)} hrs`;
}
