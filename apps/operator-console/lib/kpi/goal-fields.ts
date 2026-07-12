import type { GoalKey } from "@/lib/bq/writes";

export type GoalFieldKind = "dollars" | "percent" | "days" | "minutes" | "count";

export interface GoalField {
  key: GoalKey;
  label: string;
  kind: GoalFieldKind;
  helpText: string;
}

// Single source of truth for every goal field's editing metadata (used by
// both the bulk GoalsDrawer and Home's inline per-metric edit). `kind`
// drives the input adornment ($/%/min) and which conversion function below
// applies at the storage boundary — health.ts and the /bhaga-cloud Slack
// `config set` path both read/write the raw fraction for percent goals, so
// conversion only happens here, never upstream.
//
// Issue #158 Home scorecard fields only — legacy food-cost / on-time /
// runway keys remain in GOAL_KEYS for Slack writes but are not in this
// drawer list.
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
    key: "goal_hourly_labor_pct_max",
    label: "Part-time labor % of net sales — max",
    kind: "percent",
    helpText: "Enter a whole percent, e.g. 12. Hourly / part-time labor only.",
  },
  {
    key: "goal_labor_pct_max",
    label: "Total labor % of net sales — max",
    kind: "percent",
    helpText: "Enter a whole percent, e.g. 15. Total labor: hourly + salaried/manager.",
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
