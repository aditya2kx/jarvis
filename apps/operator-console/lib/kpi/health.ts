import "server-only";
import {
  laborDaily,
  storeConfig,
  orderQualityDaily,
  orderRecoCombined,
  type LaborDailyRow,
  type StoreConfigRow,
} from "@/lib/bq/queries";
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
  const [rows, config, quality, reco] = await Promise.all([
    laborDaily(days),
    storeConfig(DEFAULT_STORE),
    orderQualityDaily(days),
    orderRecoCombined(),
  ]);

  const netSales = sum(rows, (r) => r.net_sales);
  const laborCost = sum(rows, (r) => r.total_labor_cost);
  const laborPct = netSales && laborCost != null ? laborCost / netSales : null;

  // On-time speed = complement of the % of KDS tickets that missed the prep
  // goal, averaged over the window — same field the Order Quality screen
  // charts (vw_order_quality_daily.kds_pct_tickets_late).
  const onTimePct = avg(quality, (r) =>
    r.kds_pct_tickets_late != null ? 1 - r.kds_pct_tickets_late : null,
  );

  // Runway = the tightest (smallest) "Days Left" across tracked items in the
  // nearer of the two registered delivery slots — vw_order_reco_combined
  // (migration 032), the same view the Inventory screen reads. No food-cost
  // source exists yet (no COGS table) — left null/"—", matching the design's
  // own placeholder dash for that row rather than fabricating a number.
  const runwayDays = reco.length
    ? Math.min(
        ...reco
          .map((r) => r["Days Left 1"] ?? r["Days Left 2"])
          .filter((d): d is number => d != null),
      )
    : null;

  const goalNetSales = goalValue(config, window === "weekly" ? "goal_net_sales_weekly" : "goal_net_sales_monthly");
  const goalLaborPctMax = goalValue(config, "goal_labor_pct_max");
  const goalFoodCostMax = goalValue(config, "goal_food_cost_pct_max");
  const goalOnTimeMin = goalValue(config, "goal_speed_on_time_pct_min");
  const goalRunwayMin = goalValue(config, "goal_inventory_runway_days_min");

  const netSalesPace = paceFor(netSales, goalNetSales, false);
  const laborPctPace = paceFor(laborPct, goalLaborPctMax, true);
  const onTimePace = paceFor(onTimePct, goalOnTimeMin, false);
  const runwayPace = paceFor(runwayDays, goalRunwayMin, false);

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
    {
      key: "food_cost_pct",
      label: "Food cost %",
      actual: null,
      goal: goalFoodCostMax,
      status: "no-goal",
      pace: null,
      formatted: "—",
      goalFormatted: fmtPct(goalFoodCostMax),
    },
    {
      key: "speed_on_time_pct",
      label: "Speed — on-time %",
      actual: onTimePct,
      goal: goalOnTimeMin,
      status: statusFor(onTimePace),
      pace: onTimePace,
      formatted: fmtPct(onTimePct),
      goalFormatted: fmtPct(goalOnTimeMin),
    },
    {
      key: "inventory_runway_days",
      label: "Inventory runway",
      actual: runwayDays,
      goal: goalRunwayMin,
      status: statusFor(runwayPace),
      pace: runwayPace,
      formatted: runwayDays == null ? "—" : `${runwayDays.toFixed(1)} d`,
      goalFormatted: goalRunwayMin == null ? "—" : `${goalRunwayMin.toFixed(0)} d`,
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

function avg<T>(rows: T[], pick: (r: T) => number | null | undefined): number | null {
  const vals = rows.map(pick).filter((v): v is number => v != null);
  if (!vals.length) return null;
  return vals.reduce((s, v) => s + v, 0) / vals.length;
}

function fmtDollars(n: number | null): string {
  return n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function fmtPct(n: number | null): string {
  return n == null ? "—" : `${(n * 100).toFixed(1)}%`;
}
