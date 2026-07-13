import type { GoalKey } from "@/lib/bq/writes";

export type GoalFieldKind = "dollars" | "percent" | "days" | "minutes" | "count";

export interface GoalField {
  key: GoalKey;
  label: string;
  kind: GoalFieldKind;
  helpText: string;
}

// Home Goal and Tracking hierarchy (Issue #158 operator feedback) — Finance /
// Top line / Cost / Quality / Inventory. Legacy food-cost / on-time / runway /
// labor-% keys remain in GOAL_KEYS for Slack but are not in this drawer list.
export const GOAL_FIELDS: GoalField[] = [
  {
    key: "goal_cash_flow_weekly",
    label: "Cash flow — weekly target",
    kind: "dollars",
    helpText: "Net sales − Plaid outflows, weekly, e.g. 12000",
  },
  {
    key: "goal_cash_flow_monthly",
    label: "Cash flow — monthly target",
    kind: "dollars",
    helpText: "Net sales − Plaid outflows, monthly, e.g. 50000",
  },
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
    key: "goal_orders_weekly",
    label: "Orders — weekly target",
    kind: "count",
    helpText: "Weekly order count target, e.g. 900",
  },
  {
    key: "goal_orders_monthly",
    label: "Orders — monthly target",
    kind: "count",
    helpText: "Monthly order count target, e.g. 3600",
  },
  {
    key: "goal_total_cost_weekly",
    label: "Total known cost — weekly max",
    kind: "dollars",
    helpText: "Labor + Plaid ops (excludes COGS). Weekly max, e.g. 10000",
  },
  {
    key: "goal_total_cost_monthly",
    label: "Total known cost — monthly max",
    kind: "dollars",
    helpText: "Labor + Plaid ops (excludes COGS). Monthly max, e.g. 40000",
  },
  {
    key: "goal_labor_cost_weekly",
    label: "Labor cost — weekly max",
    kind: "dollars",
    helpText: "Total labor $ weekly max, e.g. 4500",
  },
  {
    key: "goal_labor_cost_monthly",
    label: "Labor cost — monthly max",
    kind: "dollars",
    helpText: "Total labor $ monthly max, e.g. 18000",
  },
  {
    key: "goal_ops_cost_weekly",
    label: "Operations / other — weekly max",
    kind: "dollars",
    helpText: "Plaid outflows weekly max, e.g. 5000",
  },
  {
    key: "goal_ops_cost_monthly",
    label: "Operations / other — monthly max",
    kind: "dollars",
    helpText: "Plaid outflows monthly max, e.g. 20000",
  },
  {
    key: "goal_kds_p95_min",
    label: "Prep time p95 — max minutes",
    kind: "minutes",
    helpText: "KDS per-item p95 prep time goal in minutes, e.g. 8",
  },
  {
    key: "goal_bases_at_risk_max",
    label: "Bases at risk — max count",
    kind: "count",
    helpText: "Max Risky bases (stockout before restock). Goal is usually 0.",
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

/** Caps a dollars-kind input to at most 2 decimal places as the operator
 *  types (e.g. "50.999" -> "50.99") — dollar amounts don't have a
 *  sub-cent unit, so the input shouldn't let one accumulate in the first
 *  place, matching the operator feedback that amounts must not show more
 *  precision than cents. */
export function sanitizeDollarInput(raw: string): string {
  const s = raw.replace(/[^0-9.]/g, "");
  const firstDot = s.indexOf(".");
  if (firstDot === -1) return s;
  const whole = s.slice(0, firstDot);
  const frac = s.slice(firstDot + 1).replace(/\./g, "").slice(0, 2);
  return `${whole}.${frac}`;
}
