import type {
  PayrollPeriodRow,
  PayrollSoloPremiumRow,
} from "@/lib/bq/queries";

export interface PayrollRowWithSolo extends PayrollPeriodRow {
  /** Premium-bearing solo hours, clamped to regular hours. */
  solo_hours: number;
  /** Premium dollars for those hours. Derived from integer cents. */
  solo_premium: number;
  /**
   * Wages for hours paid at the solo rate — the whole rate-2 line, not the
   * uplift. `solo_hours x premium_rate`, which is the amount ADP shows for that
   * line rather than the difference from base.
   */
  solo_wages: number;
  /**
   * Wages for everything not on the solo rate: non-solo regular hours at base,
   * plus overtime. OT lives here so `primary + solo` reconciles exactly to total
   * wages; mixed-rate OT is FLSA work deferred to #315 and no eligible employee
   * has hit overtime yet.
   */
  primary_wages: number;
  /** Primary + solo. The blended-rate figure, and what ADP will gross to. */
  total_wages: number;
  /**
   * Hours worked away from the shop (migration 072). A slice of `hours_worked`,
   * not an addition to it, and never premium-bearing — remote time is not floor
   * coverage. Shown so an operator can see why someone has hours but no solo
   * time, instead of reading it as a missing premium.
   */
  remote_hours: number;
  /** `hours_worked - remote_hours`. Time actually in the shop. */
  on_floor_hours: number;
}

export interface SoloPremiumMerge {
  rows: PayrollRowWithSolo[];
  /** Integer cents — format with formatCents, never formatDollars. */
  premiumCents: number;
  soloHours: number;
  people: number;
  /** Wage totals, summed from the displayed per-row values. */
  soloWages: number;
  primaryWages: number;
  totalWages: number;
  /** Period total of hours worked away from the shop. */
  remoteHours: number;
  /**
   * Total wages / total hours, or null with no hours. Reported rather than
   * assumed: with only some hours at the premium the effective rate is neither
   * posted rate, and it is the number that explains why total wages exceed
   * hours x base.
   */
  blendedRate: number | null;
}

/**
 * Match the payroll view's employee to the solo view's.
 *
 * Both sides are alias-resolved canonical names, so an exact match should hold.
 * Normalising anyway mirrors `payroll_draft_backend.name_key`, because the
 * failure mode of a near-miss here is a silent $0 premium rather than an error —
 * and this roster genuinely varies on the middle initial ("Dolce J Johnson").
 */
function nameKey(name: string): string {
  const plain = (name || "").trim().toLowerCase().replace(/\s+/g, " ");
  const stripped = plain
    .replace(/\b[a-z]\.?\b/g, "")
    .replace(/\s+/g, " ")
    .trim();
  // Dropping initials can empty the key entirely (a single-letter name). Two
  // different people both keying to "" would cross-match and pay the wrong
  // person, so fall back to the un-stripped name rather than risk a collision.
  return stripped || plain;
}

/**
 * Attach the solo-shift premium (#309) to the per-employee payroll rows.
 *
 * The premium is folded into `est_total_pay` because it is money ADP will pay
 * once the rate-2 lines are keyed: leaving it out would make the draft
 * guardrail flag the difference between our estimate and ADP's Preview gross as
 * a mismatch on every solo payroll. `est_gross_pay` is left alone — it is the
 * view's hours x base-rate figure, and the premium is surfaced as its own column
 * and stat so the headline numbers still sum to Total pay.
 *
 * Hours are clamped to regular (non-OT) hours, the same clamp
 * `payroll_draft_backend._solo_premium_hours` applies, so the console shows
 * exactly the hours the operator keys. Solo minutes are punch-derived while
 * regular hours come from the payroll view after OT is split out, so a late
 * punch edit can otherwise put solo above what the employee is paid for.
 *
 * Given `premiumDeltaDollars`, the money is computed once from the keyed minutes
 * — which is what ADP will do from the rate-2 line. The stored `premium_cents`
 * is a sum of per-day roundings, so it lands a penny below the hours it
 * describes (1.37 h reading as $1.36 at a $1.00/h uplift). Without the delta the
 * stored cents are used as-is, scaled by the clamp, rather than guessing a rate.
 *
 * Rows with no solo data are returned untouched with explicit zeros, so a
 * missing solo table degrades to "no premium" rather than blanking the table.
 */
export function mergeSoloPremium(
  rows: PayrollPeriodRow[],
  solo: PayrollSoloPremiumRow[],
  premiumDeltaDollars: number | null = null,
): SoloPremiumMerge {
  const byName = new Map<string, PayrollSoloPremiumRow>();
  for (const s of solo ?? []) {
    byName.set(nameKey(String(s.employee ?? "")), s);
  }

  let premiumCents = 0;
  let soloHours = 0;
  let people = 0;
  let soloWages = 0;
  let primaryWages = 0;
  let totalHours = 0;
  let remoteHours = 0;

  const merged = rows.map((row) => {
    const gross = Number(row.est_gross_pay) || 0;
    const worked = Number(row.hours_worked) || 0;
    totalHours += worked;
    const match = byName.get(nameKey(String(row.employee ?? "")));

    // Remote is a property of where the shift was worked, not of pay rate, so it
    // is read before the eligibility branch: an employee at $16.25 can work
    // remote too, and the column would otherwise be blank for them.
    const remote = Math.min(Number(match?.remote_hours) || 0, worked);
    remoteHours += remote;
    const location = {
      remote_hours: remote,
      on_floor_hours: Math.round((worked - remote) * 100) / 100,
    };

    if (!match || !match.eligible) {
      // No solo rate in play: every wage dollar is primary, so the breakdown
      // still adds up for employees who are not eligible at all.
      primaryWages += gross;
      return {
        ...row,
        solo_hours: 0,
        solo_premium: 0,
        solo_wages: 0,
        primary_wages: gross,
        total_wages: gross,
        ...location,
      };
    }

    // Aggregate in whole minutes and round once. Summing the view's per-day
    // 2-decimal hours drifts a cent off the summed integer cents, and at a
    // $1.00/h premium the two are the same quantity on screen — a visible penny
    // gap between "12.24h solo" and "$12.23" reads as a bug.
    const rawMinutes = Number(match.solo_minutes) || 0;
    const rawCents = Number(match.premium_cents) || 0;
    const regularMinutes = Math.max(
      Math.round(
        ((Number(row.hours_worked) || 0) - (Number(row.ot_hours) || 0)) * 60,
      ),
      0,
    );
    const keyed = Math.min(rawMinutes, regularMinutes);
    const cents =
      premiumDeltaDollars != null
        ? Math.round((keyed / 60) * premiumDeltaDollars * 100)
        : rawMinutes > 0
          ? Math.round(rawCents * (keyed / rawMinutes))
          : 0;

    const hours = Math.round((keyed / 60) * 100) / 100;

    // The rate-2 line is the whole amount for those hours, so it needs the rate
    // itself, not the uplift. Derive it from the row's own base rate plus the
    // configured delta; falling back to splitting `gross` by hours would use a
    // blended figure that already contains OT at 1.5x and overstate the line.
    const baseRate = Number(row.wage_rate_dollars) || 0;
    const soloRate =
      premiumDeltaDollars != null && baseRate > 0
        ? baseRate + premiumDeltaDollars
        : baseRate;
    const soloWage = Math.round(hours * soloRate * 100) / 100;
    // Primary is the remainder rather than a second product: total wages are
    // fixed by `gross + premium`, and subtracting keeps the two parts summing to
    // it exactly instead of drifting a cent on rounding.
    const totalWage = Math.round((gross + cents / 100) * 100) / 100;
    const primaryWage = Math.round((totalWage - soloWage) * 100) / 100;

    soloWages += soloWage;
    primaryWages += primaryWage;

    if (keyed > 0) {
      // Totals accumulate the per-row displayed values, not the underlying
      // minutes, so the summary is what an operator gets by adding the visible
      // column up. Sum-of-rounded differs from round-of-sum by a cent, and the
      // sum-of-rounded is also what ADP pays — it rounds each rate line.
      soloHours += hours;
      premiumCents += cents;
      people += 1;
    }

    return {
      ...row,
      solo_hours: hours,
      solo_premium: cents / 100,
      solo_wages: soloWage,
      primary_wages: primaryWage,
      total_wages: totalWage,
      est_total_pay: (Number(row.est_total_pay) || 0) + cents / 100,
      ...location,
    };
  });

  const totalWages = Math.round((primaryWages + soloWages) * 100) / 100;

  return {
    rows: merged,
    premiumCents,
    soloHours: Math.round(soloHours * 100) / 100,
    people,
    soloWages: Math.round(soloWages * 100) / 100,
    primaryWages: Math.round(primaryWages * 100) / 100,
    totalWages,
    remoteHours: Math.round(remoteHours * 100) / 100,
    blendedRate:
      totalHours > 0 ? Math.round((totalWages / totalHours) * 100) / 100 : null,
  };
}
