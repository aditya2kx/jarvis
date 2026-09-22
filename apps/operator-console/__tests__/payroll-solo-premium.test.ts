import { describe, expect, it } from "vitest";
import { mergeSoloPremium } from "@/lib/payroll/solo-premium";
import type {
  PayrollPeriodRow,
  PayrollSoloPremiumRow,
} from "@/lib/bq/queries";

function payrollRow(over: Partial<PayrollPeriodRow> = {}): PayrollPeriodRow {
  return {
    period_start: "2026-09-07",
    period_end: "2026-09-20",
    is_open: true,
    employee: "Brooke Willingham",
    labor_type: "parttime",
    wage_rate_dollars: 15.25,
    ot_rate_dollars: 22.88,
    hours_worked: 40,
    ot_hours: 0,
    est_gross_pay: 610,
    tips_allocated: 20,
    review_bonus: 0,
    recognition_bonus: 0,
    recognition_reason: null,
    perks: 0,
    perk_reason: null,
    est_total_pay: 630,
    adp_wages_paid: 0,
    adp_tips_paid: 0,
    adp_bonus_paid: 0,
    adp_total_paid: 0,
    wage_diff: 0,
    tip_diff: 0,
    bonus_diff: 0,
    ...over,
  } as PayrollPeriodRow;
}

function soloRow(
  over: Partial<PayrollSoloPremiumRow> = {},
): PayrollSoloPremiumRow {
  const base = {
    employee: "Brooke Willingham",
    solo_hours: 4.8,
    team_hours: 35.2,
    total_hours: 40,
    base_rate_dollars: 15.25,
    eligible: true,
    premium_cents: 480,
    remote_hours: 0,
    on_floor_hours: 40,
    ...over,
  };
  // Minutes are the authoritative grain; default them from hours unless the
  // caller is exercising the hours/minutes disagreement directly.
  return {
    solo_minutes: Math.round(base.solo_hours * 60),
    ...base,
  } as PayrollSoloPremiumRow;
}

describe("mergeSoloPremium", () => {
  it("attaches solo hours and premium, and folds the premium into Est. total", () => {
    const m = mergeSoloPremium([payrollRow()], [soloRow()]);

    expect(m.rows[0].solo_hours).toBe(4.8);
    expect(m.rows[0].solo_premium).toBe(4.8);
    expect(m.premiumCents).toBe(480);
    expect(m.soloHours).toBe(4.8);
    expect(m.people).toBe(1);
    // Est. total gains the premium; Est. wages stays hours x base rate.
    expect(m.rows[0].est_total_pay).toBeCloseTo(634.8, 2);
    expect(m.rows[0].est_gross_pay).toBe(610);
  });

  it("pays nothing to an ineligible employee even when they worked alone", () => {
    // Already at $16.25 — there is no premium to add, and paying a zero-hour
    // line would still be wrong (it would imply eligibility).
    const m = mergeSoloPremium(
      [payrollRow({ employee: "Ximena Ortiz", wage_rate_dollars: 16.25 })],
      [
        soloRow({
          employee: "Ximena Ortiz",
          base_rate_dollars: 16.25,
          eligible: false,
          premium_cents: 0,
        }),
      ],
    );

    expect(m.rows[0].solo_hours).toBe(0);
    expect(m.rows[0].solo_premium).toBe(0);
    expect(m.premiumCents).toBe(0);
    expect(m.people).toBe(0);
    expect(m.rows[0].est_total_pay).toBe(630);
  });

  it("clamps solo hours to regular hours and scales the premium with them", () => {
    // 40 paid, 6 of them OT -> 34 regular. Punch-derived solo of 36 cannot all
    // be keyed at the premium rate; the operator keys 34.
    const m = mergeSoloPremium(
      [payrollRow({ hours_worked: 40, ot_hours: 6 })],
      [soloRow({ solo_hours: 36, premium_cents: 3600 })],
    );

    expect(m.rows[0].solo_hours).toBe(34);
    expect(m.premiumCents).toBe(3400);
    expect(m.rows[0].solo_premium).toBe(34);
  });

  it("matches employees whose middle initial differs between the two views", () => {
    const m = mergeSoloPremium(
      [payrollRow({ employee: "Dolce Johnson" })],
      [soloRow({ employee: "Dolce J Johnson", solo_hours: 2, premium_cents: 200 })],
    );

    expect(m.rows[0].solo_premium).toBe(2);
    expect(m.premiumCents).toBe(200);
  });

  it("does not cross-match two people whose names reduce to the same empty key", () => {
    // Stripping initials empties a single-letter name; without a fallback both
    // would key to "" and the first solo row would pay the wrong person.
    const m = mergeSoloPremium(
      [payrollRow({ employee: "X" }), payrollRow({ employee: "Y" })],
      [soloRow({ employee: "X", solo_hours: 3, premium_cents: 300 })],
    );

    expect(m.rows[0].solo_premium).toBe(3);
    expect(m.rows[1].solo_premium).toBe(0);
    expect(m.premiumCents).toBe(300);
  });

  it("leaves rows untouched when the solo table returned nothing", () => {
    const m = mergeSoloPremium([payrollRow()], []);

    expect(m.rows[0].solo_hours).toBe(0);
    expect(m.rows[0].est_total_pay).toBe(630);
    expect(m.premiumCents).toBe(0);
  });

  it("totals the premium across employees", () => {
    const m = mergeSoloPremium(
      [
        payrollRow({ employee: "Ariana Reyes" }),
        payrollRow({ employee: "Brooke Willingham" }),
        payrollRow({ employee: "Ximena Ortiz" }),
      ],
      [
        soloRow({ employee: "Ariana Reyes", solo_hours: 1.75, premium_cents: 175 }),
        soloRow({
          employee: "Brooke Willingham",
          solo_hours: 1.1,
          premium_cents: 110,
        }),
        soloRow({
          employee: "Ximena Ortiz",
          eligible: false,
          premium_cents: 0,
        }),
      ],
    );

    expect(m.premiumCents).toBe(285);
    expect(m.soloHours).toBe(2.85);
    expect(m.people).toBe(2);
  });

  it("computes money from the rate delta so a row's dollars match its hours", () => {
    // 82 solo minutes = 1.37 h. The stored cents are a sum of per-day roundings
    // (136), which renders as $1.36 next to 1.37 h and reads as a bug at a
    // $1.00/h uplift. With the delta the money is rounded once: $1.37.
    const withDelta = mergeSoloPremium(
      [payrollRow()],
      [soloRow({ solo_minutes: 82, solo_hours: 1.37, premium_cents: 136 })],
      1.0,
    );
    expect(withDelta.rows[0].solo_hours).toBe(1.37);
    expect(withDelta.rows[0].solo_premium).toBe(1.37);
    expect(withDelta.premiumCents).toBe(137);

    // Without the delta the stored cents are used rather than a guessed rate.
    const noDelta = mergeSoloPremium(
      [payrollRow()],
      [soloRow({ solo_minutes: 82, solo_hours: 1.37, premium_cents: 136 })],
    );
    expect(noDelta.premiumCents).toBe(136);
  });

  it("summary equals the sum of the displayed rows, on hours and on dollars", () => {
    // The operator verifies a total by adding the visible column up, so the
    // headline must agree with the rows rather than with the underlying minutes
    // (sum-of-rounded differs from round-of-sum by a cent). Minutes here do not
    // divide evenly into hundredths of an hour, which is what exposes the gap.
    const m = mergeSoloPremium(
      [
        payrollRow({ employee: "Ariana Reyes" }),
        payrollRow({ employee: "Brooke Willingham" }),
      ],
      [
        soloRow({ employee: "Ariana Reyes", solo_minutes: 439 }),
        soloRow({ employee: "Brooke Willingham", solo_minutes: 295 }),
      ],
      1.0,
    );

    const rowHours = m.rows.reduce((s, r) => s + r.solo_hours, 0);
    const rowDollars = m.rows.reduce((s, r) => s + r.solo_premium, 0);

    expect(m.soloHours).toBeCloseTo(rowHours, 2);
    expect(m.premiumCents / 100).toBeCloseTo(rowDollars, 2);
    // 439 min -> 7.32 h, 295 min -> 4.92 h.
    expect(m.soloHours).toBe(12.24);
    expect(m.premiumCents).toBe(1224);
  });
});

describe("wage split by rate", () => {
  it("prices solo hours at the full solo rate, not the uplift", () => {
    const m = mergeSoloPremium([payrollRow()], [soloRow()], 1.0);
    const row = m.rows[0];
    // 4.8 h at $16.25 is the whole rate-2 line, not 4.8 x $1.00.
    expect(row.solo_wages).toBe(78);
    expect(row.primary_wages).toBe(536.8);
    expect(row.total_wages).toBe(614.8);
  });

  it("keeps primary + solo exactly equal to total wages", () => {
    const m = mergeSoloPremium(
      [
        payrollRow({ employee: "Brooke Willingham" }),
        payrollRow({
          employee: "Jacob Garcia",
          hours_worked: 39.98,
          est_gross_pay: 609.7,
        }),
      ],
      [soloRow(), soloRow({ employee: "Jacob Garcia", solo_hours: 7.32, premium_cents: 732 })],
      1.0,
    );
    for (const row of m.rows) {
      expect(row.primary_wages + row.solo_wages).toBeCloseTo(row.total_wages, 2);
    }
    expect(m.primaryWages + m.soloWages).toBeCloseTo(m.totalWages, 2);
  });

  it("counts an ineligible employee's whole wage as primary", () => {
    const m = mergeSoloPremium(
      [payrollRow({ employee: "Dolce J Johnson", est_gross_pay: 650 })],
      [soloRow({ employee: "Dolce J Johnson", eligible: false })],
      1.0,
    );
    expect(m.rows[0].primary_wages).toBe(650);
    expect(m.rows[0].solo_wages).toBe(0);
    expect(m.primaryWages).toBe(650);
  });

  it("charges overtime to primary so the blended rate stays above base", () => {
    // OT is 1.5x and is not split by rate (#315), so it belongs in primary —
    // otherwise primary + solo would not reconcile to the view's gross.
    const m = mergeSoloPremium(
      [payrollRow({ hours_worked: 44, ot_hours: 4, est_gross_pay: 701.52 })],
      [soloRow()],
      1.0,
    );
    expect(m.rows[0].solo_wages).toBe(78);
    expect(m.rows[0].primary_wages).toBe(628.32);
    // $706.32 over 44 h — above base because of both the OT premium and solo.
    expect(m.blendedRate).toBe(16.05);
  });

  it("reports no blended rate when there are no hours", () => {
    const m = mergeSoloPremium([], [], 1.0);
    expect(m.blendedRate).toBeNull();
    expect(m.totalWages).toBe(0);
  });

  describe("remote hours (migration 072)", () => {
    it("splits total hours into remote and on-floor without changing pay", () => {
      const m = mergeSoloPremium(
        [payrollRow({ hours_worked: 40, est_gross_pay: 610 })],
        [soloRow({ solo_hours: 0, premium_cents: 0, remote_hours: 8 })],
        1.0,
      );
      const row = m.rows[0];
      expect(row.remote_hours).toBe(8);
      expect(row.on_floor_hours).toBe(32);
      // Remote is a slice of hours worked, so wages are untouched.
      expect(row.hours_worked).toBe(40);
      expect(row.total_wages).toBe(610);
      expect(m.remoteHours).toBe(8);
    });

    it("reports remote hours for an ineligible employee too", () => {
      // Remote is about where the shift was worked, not the pay rate. Reading it
      // after the eligibility branch would blank the column for anyone at $16.25.
      const m = mergeSoloPremium(
        [payrollRow({ wage_rate_dollars: 16.25 })],
        [
          soloRow({
            base_rate_dollars: 16.25,
            eligible: false,
            solo_hours: 0,
            premium_cents: 0,
            remote_hours: 6.5,
          }),
        ],
        1.0,
      );
      expect(m.rows[0].remote_hours).toBe(6.5);
      expect(m.rows[0].solo_premium).toBe(0);
      expect(m.remoteHours).toBe(6.5);
    });

    it("clamps remote to hours worked so a punch edit cannot exceed the total", () => {
      const m = mergeSoloPremium(
        [payrollRow({ hours_worked: 5, est_gross_pay: 76.25 })],
        [soloRow({ solo_hours: 0, premium_cents: 0, remote_hours: 8 })],
        1.0,
      );
      expect(m.rows[0].remote_hours).toBe(5);
      expect(m.rows[0].on_floor_hours).toBe(0);
    });

    it("defaults to zero remote when the solo view predates the migration", () => {
      const m = mergeSoloPremium(
        [payrollRow()],
        [soloRow({ remote_hours: undefined as unknown as number })],
        1.0,
      );
      expect(m.rows[0].remote_hours).toBe(0);
      expect(m.rows[0].on_floor_hours).toBe(40);
      expect(m.remoteHours).toBe(0);
    });

    it("zeroes remote for an employee with no solo row at all", () => {
      const m = mergeSoloPremium([payrollRow()], [], 1.0);
      expect(m.rows[0].remote_hours).toBe(0);
      expect(m.rows[0].on_floor_hours).toBe(40);
    });
  });
});
