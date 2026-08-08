/**
 * Labor hours chart Y-axis unit — Hours (default) vs % of Square net sales.
 * URL `?unit=hours|pct` via FilterPills (Issue #227).
 */

export type LaborChartUnit = "hours" | "pct";

export const LABOR_CHART_UNIT_OPTIONS: { value: LaborChartUnit; label: string }[] = [
  { value: "hours", label: "Hours" },
  { value: "pct", label: "% of net sales" },
];

function firstValue(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

/** `unit=pct` → pct; missing / other → hours. */
export function parseLaborChartUnit(
  value: string | string[] | undefined,
): LaborChartUnit {
  const raw = firstValue(value)?.trim().toLowerCase();
  return raw === "pct" ? "pct" : "hours";
}
