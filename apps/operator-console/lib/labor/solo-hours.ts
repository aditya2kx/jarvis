import type { LaborSoloHoursRow } from "@/lib/bq/queries";

export interface SoloHoursSummary {
  /** Rows to show: only people who actually worked alone in the Period. */
  rows: LaborSoloHoursRow[];
  soloHours: number;
  /** Integer cents — format with formatCents, never formatDollars. */
  premiumCents: number;
  people: number;
  /** Hours worked away from the shop in the window (migration 072). */
  remoteHours: number;
}

/**
 * Roll the solo-hours rows into the Period summary shown under the table.
 *
 * Rows with zero solo hours are dropped rather than rendered as "0.00": every
 * employee who punched in the Period appears in the source rows, so keeping them
 * would bury the handful of people the premium is about in a full roster.
 *
 * People who worked remote are kept even with zero solo hours: their remote
 * shift is the reason a coworker shows up as solo, so hiding them would make the
 * panel look self-contradictory (someone alone while a colleague was clocked in).
 *
 * The premium total sums `premium_cents` rather than deriving it from hours,
 * because eligibility is per-employee — summing hours and multiplying would pay
 * a premium to people already above the eligible base rate.
 */
export function summarizeSoloHours(
  rows: LaborSoloHoursRow[],
): SoloHoursSummary {
  const shown = rows.filter(
    (r) => (Number(r.solo_hours) || 0) > 0 || (Number(r.remote_hours) || 0) > 0,
  );
  const withSolo = shown.filter((r) => (Number(r.solo_hours) || 0) > 0);
  return {
    rows: shown,
    soloHours:
      Math.round(
        withSolo.reduce((sum, r) => sum + (Number(r.solo_hours) || 0), 0) * 100,
      ) / 100,
    premiumCents: withSolo.reduce(
      (sum, r) => sum + (Number(r.premium_cents) || 0),
      0,
    ),
    // Counts people with solo hours, not rows shown: a remote-only row is
    // context, and counting it would overstate who the premium is about.
    people: withSolo.length,
    remoteHours:
      Math.round(
        shown.reduce((sum, r) => sum + (Number(r.remote_hours) || 0), 0) * 100,
      ) / 100,
  };
}
