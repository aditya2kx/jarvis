import "server-only";
import { laborDaily, storeConfig, type LaborDailyRow, type StoreConfigRow } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";

export type GoalStatus = "on-track" | "at-risk" | "off-track" | "no-goal";

export interface HealthMetric {
  key: string;
  label: string;
  actual: number | null;
  goal: number | null;
  status: GoalStatus;
  pace: number | null;
  formatted: string;
  goalFormatted: string;
}

function goalValue(config: StoreConfigRow[], key: string): number | null {
  const row = config.find((r) => r.key === key);
  return row ? Number(row.value) : null;
}

// Presentation-only comparison of already-fetched rows against a
// `store_config` goal — not a new metric (per EXECUTION.md §4 M2 step 3:
// "computes status/pace in the component from already-fetched rows only").
function paceFor(actual: number | null, goal: number | null, lowerIsBetter: boolean): number | null {
  if (actual == null || goal == null || goal === 0) return null;
  return lowerIsBetter ? goal / actual : actual / goal;
}

function statusFor(pace: number | null): GoalStatus {
  if (pace == null) return "no-goal";
  if (pace >= 1) return "on-track";
  if (pace >= 0.85) return "at-risk";
  return "off-track";
}

export interface HealthScorecard {
  window: "weekly" | "monthly";
  metrics: HealthMetric[];
  windowLabel: string;
}

export async function loadHealthScorecard(window: "weekly" | "monthly"): Promise<HealthScorecard> {
  const days = window === "weekly" ? 7 : 30;
  const [rows, config] = await Promise.all([laborDaily(days), storeConfig(DEFAULT_STORE)]);

  const netSales = sum(rows, (r) => r.net_sales);
  const laborCost = sum(rows, (r) => r.total_labor_cost);
  const laborPct = netSales && laborCost != null ? laborCost / netSales : null;

  const goalNetSales = goalValue(config, window === "weekly" ? "goal_net_sales_weekly" : "goal_net_sales_monthly");
  const goalLaborPctMax = goalValue(config, "goal_labor_pct_max");

  const netSalesPace = paceFor(netSales, goalNetSales, false);
  const laborPctPace = paceFor(laborPct, goalLaborPctMax, true);

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
    },
    {
      key: "labor_pct",
      label: "Labor % of net sales",
      actual: laborPct,
      goal: goalLaborPctMax,
      status: statusFor(laborPctPace),
      pace: laborPctPace,
      formatted: fmtPct(laborPct),
      goalFormatted: fmtPct(goalLaborPctMax),
    },
  ];

  return {
    window,
    metrics,
    windowLabel: window === "weekly" ? "This week (last 7 days)" : "This month (last 30 days)",
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
