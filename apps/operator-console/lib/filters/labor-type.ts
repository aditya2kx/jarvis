/** Labor bucket multi-select (PT / FT) for the Labor page URL. */

import {
  normalizeSourceSelection,
  parseSources,
  serializeSources,
} from "@/lib/filters/sources";

export const LABOR_TYPE_OPTIONS = ["Part-time", "Full-time"] as const;
export type LaborTypeOption = (typeof LABOR_TYPE_OPTIONS)[number];

/** Same contract as Square sources: null=all, []=none, else partial. */
export function parseLaborTypes(value: string | string[] | undefined): string[] | null {
  const parsed = parseSources(value);
  if (parsed == null) return null;
  const allowed = new Set<string>(LABOR_TYPE_OPTIONS);
  const filtered = parsed.filter((v) => allowed.has(v));
  return normalizeSourceSelection(filtered, [...LABOR_TYPE_OPTIONS]);
}

export function serializeLaborTypes(selected: string[] | null): string {
  return serializeSources(selected);
}

export function normalizeLaborTypeSelection(selected: string[]): string[] | null {
  return normalizeSourceSelection(selected, [...LABOR_TYPE_OPTIONS]);
}

export function showsPartTime(selected: string[] | null): boolean {
  return selected == null || selected.includes("Part-time");
}

export function showsFullTime(selected: string[] | null): boolean {
  return selected == null || selected.includes("Full-time");
}
