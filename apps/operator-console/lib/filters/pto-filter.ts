/** Scheduled-hours PTO filter for the Labor page URL (`pto` query param). */

export const PTO_FILTER_OPTIONS = [
  { value: "include", label: "Include PTO" },
  { value: "exclude", label: "Exclude PTO" },
] as const;

export type PtoFilter = "include" | "exclude";

/** Default = include (ADP footer parity). Only `exclude` is sticky in the URL. */
export function parsePtoFilter(value: string | undefined): PtoFilter {
  return value === "exclude" ? "exclude" : "include";
}

export function serializePtoFilter(value: PtoFilter): string {
  return value === "exclude" ? "exclude" : "";
}
