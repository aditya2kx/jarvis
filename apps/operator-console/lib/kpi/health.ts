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
import { isMonthLike, type DateWindow } from "@/lib/filters/range";
import type { GoalKey } from "@/lib/bq/writes";

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
  win: DateWindow;
  metrics: HealthMetric[];
  windowLabel: string;
}

export async function loadHealthScorecard(win: DateWindow): Promise<HealthScorecard> {
  const [rows, config, quality, reco] = await Promise.all([
    laborDaily(win),
    storeConfig(DEFAULT_STORE),
    orderQualityDaily(win),
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
  const runwayCandidates = reco
    .map((r) => r["Days Left 1"] ?? r["Days Left 2"])
    .filter((d): d is number => d != null);
  const runwayDays = runwayCandidates.length ? Math.min(...runwayCandidates) : null;

  const netSalesGoalKey: GoalKey = isMonthLike(win.preset) ? "goal_net_sales_monthly" : "goal_net_sales_weekly";
  const goalNetSales = goalValue(config, netSalesGoalKey);
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
      goalKey: netSalesGoalKey,
      rawGoal: goalRaw(config, netSalesGoalKey),
      info: isMonthLike(win.preset)
        ? "Total net sales for the selected period vs the monthly target (goal_net_sales_monthly)."
        : "Total net sales for the selected period vs the weekly target (goal_net_sales_weekly).",
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
      goalKey: "goal_labor_pct_max",
      rawGoal: goalRaw(config, "goal_labor_pct_max"),
      info: "Total labor (hourly staff + salaried/manager) as a % of net sales — the Labor page also breaks out hourly-only %.",
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
      goalKey: "goal_food_cost_pct_max",
      rawGoal: goalRaw(config, "goal_food_cost_pct_max"),
      info: "Cost of goods as a % of net sales. No COGS data source is wired up yet — the goal can be set, but actual/status stay a placeholder.",
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
      goalKey: "goal_speed_on_time_pct_min",
      rawGoal: goalRaw(config, "goal_speed_on_time_pct_min"),
      info: "Share of KDS tickets that finished within the on-time prep goal — the complement of Order Quality's % tickets late.",
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
      goalKey: "goal_inventory_runway_days_min",
      rawGoal: goalRaw(config, "goal_inventory_runway_days_min"),
      info: "Tightest (smallest) Days-Left across tracked items in the nearer registered delivery slot — same source as the Inventory screen.",
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
