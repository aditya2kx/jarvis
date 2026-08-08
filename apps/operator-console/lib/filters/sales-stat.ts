/**
 * Rollup Stat for collapsing grains (weekday / hour of day) — Sales + Labor.
 * Total = SUM across the Period for that weekday/hour.
 * Average = typical day:
 *   - Hour → total / calendar days in Period
 *   - Weekday → total / count of that weekday in Period
 * URL `?stat=total|avg` (Issue #227). Default avg (natural for these grains).
 */

export type SalesStat = "total" | "avg";
/** Alias — same Total|Average control on Labor. */
export type RollupStat = SalesStat;

export const SALES_STAT_OPTIONS: { value: SalesStat; label: string }[] = [
  { value: "avg", label: "Average" },
  { value: "total", label: "Total" },
];
export const ROLLUP_STAT_OPTIONS = SALES_STAT_OPTIONS;

function firstValue(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

/** Missing / `avg` → avg; only explicit `total` sticks. */
export function parseSalesStat(value: string | string[] | undefined): SalesStat {
  const raw = firstValue(value)?.trim().toLowerCase();
  return raw === "total" ? "total" : "avg";
}
export const parseRollupStat = parseSalesStat;

/** Weekday / Hour of day collapse many calendar days — Total|Average applies. */
export function salesStatApplicable(grain: string): boolean {
  return grain === "weekday" || grain === "hour";
}
export const rollupStatApplicable = salesStatApplicable;

