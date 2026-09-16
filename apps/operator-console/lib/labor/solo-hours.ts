import type { LaborSoloHoursRow } from "@/lib/bq/queries";

export interface SoloHoursSummary {
  /** Rows to show: only people who actually worked alone in the Period. */
  rows: LaborSoloHoursRow[];
  soloHours: number;
  /** Integer cents — format with formatCents, never formatDollars. */
  premiumCents: number;
  people: number;
}

/**
 * Roll the solo-hours rows into the Period summary shown under the table.
 *
 * Rows with zero solo hours are dropped rather than rendered as "0.00": every
 * employee who punched in the Period appears in the source rows, so keeping them
 * would bury the handful of people the premium is about in a full roster.
 *
 * The premium total sums `premium_cents` rather than deriving it from hours,
 * because eligibility is per-employee — summing hours and multiplying would pay
 * a premium to people already above the eligible base rate.
 */
export function summarizeSoloHours(
  rows: LaborSoloHoursRow[],
): SoloHoursSummary {
  const withSolo = rows.filter((r) => (Number(r.solo_hours) || 0) > 0);
  return {
    rows: withSolo,
    soloHours:
      Math.round(
        withSolo.reduce((sum, r) => sum + (Number(r.solo_hours) || 0), 0) * 100,
      ) / 100,
    premiumCents: withSolo.reduce(
      (sum, r) => sum + (Number(r.premium_cents) || 0),
      0,
    ),
    people: withSolo.length,
  };
}
