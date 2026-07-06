import type { GoalKey } from "@/lib/bq/writes";

export type GoalFieldKind = "dollars" | "percent" | "days";

export interface GoalField {
  key: GoalKey;
  label: string;
  kind: GoalFieldKind;
  helpText: string;
}

// Single source of truth for every goal field's editing metadata (used by
// both the bulk GoalsDrawer and Home's inline per-metric edit). `kind`
// drives the input adornment ($/%) and which conversion function below
// applies at the storage boundary — health.ts and the /bhaga-cloud Slack
// `config set` path both read/write the raw fraction, so conversion only
// happens here, never upstream.
export const GOAL_FIELDS: GoalField[] = [
  {
    key: "goal_net_sales_weekly",
    label: "Net sales — weekly target",
    kind: "dollars",
    helpText: "Weekly net sales target, e.g. 18000",
  },
  {
    key: "goal_net_sales_monthly",
    label: "Net sales — monthly target",
    kind: "dollars",
    helpText: "Monthly net sales target, e.g. 75000",
  },
  {
    key: "goal_labor_pct_max",
    label: "Labor % of net sales — max",
    kind: "percent",
    helpText: "Enter a whole percent, e.g. 15 (=15%). Total labor: hourly + salaried/manager.",
  },
  {
    key: "goal_food_cost_pct_max",
    label: "Food cost % — max",
    kind: "percent",
    helpText: "Enter a whole percent, e.g. 28. No COGS source is wired up yet, so this isn't tracked on the scorecard.",
  },
  {
    key: "goal_speed_on_time_pct_min",
    label: "On-time order speed — min",
    kind: "percent",
    helpText: "Enter a whole percent, e.g. 90. Share of KDS tickets finishing within the on-time goal.",
  },
  {
    key: "goal_inventory_runway_days_min",
    label: "Inventory runway — min days",
    kind: "days",
    helpText: "Minimum days of runway across tracked items, e.g. 3",
  },
];

function trimTrailingZeros(s: string): string {
  if (!s.includes(".")) return s;
  return s.replace(/0+$/, "").replace(/\.$/, "");
}

/** `store_config` fraction (e.g. "0.15") -> whole-percent input text ("15"). */
export function fractionToPercentInput(stored: string): string {
  if (stored.trim() === "") return stored;
  const n = Number(stored);
  if (!Number.isFinite(n)) return stored;
  return trimTrailingZeros((n * 100).toFixed(4));
}

/** Whole-percent input text ("15") -> `store_config` fraction ("0.15"). */
export function percentInputToFraction(input: string): string {
  if (input.trim() === "") return input;
  const n = Number(input);
  if (!Number.isFinite(n)) return input;
  return trimTrailingZeros((n / 100).toFixed(6));
}
