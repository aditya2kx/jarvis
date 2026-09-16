import { describe, expect, it } from "vitest";
import { summarizeSoloHours } from "@/lib/labor/solo-hours";
import type { LaborSoloHoursRow } from "@/lib/bq/queries";

function row(over: Partial<LaborSoloHoursRow> = {}): LaborSoloHoursRow {
  return {
    employee: "Willingham, Brooke",
    solo_hours: 0,
    team_hours: 0,
    total_hours: 0,
    base_rate_dollars: 15.25,
    eligible: true,
    premium_cents: 0,
    ...over,
  };
}

describe("summarizeSoloHours (Issue #309)", () => {
  it("keeps only people who worked alone", () => {
    const summary = summarizeSoloHours([
      row({ employee: "Alone, Amy", solo_hours: 2.5, team_hours: 1, total_hours: 3.5 }),
      row({ employee: "Never, Nick", solo_hours: 0, team_hours: 8, total_hours: 8 }),
    ]);
    expect(summary.rows.map((r) => r.employee)).toEqual(["Alone, Amy"]);
    expect(summary.people).toBe(1);
  });

  it("sums solo hours without float drift", () => {
    const summary = summarizeSoloHours([
      row({ employee: "A", solo_hours: 0.1 }),
      row({ employee: "B", solo_hours: 0.2 }),
    ]);
    expect(summary.soloHours).toBe(0.3);
  });

  it("sums premium cents rather than deriving them from hours", () => {
    // An ineligible employee accrues solo hours with no premium; deriving the
    // total from hours would pay them anyway.
    const summary = summarizeSoloHours([
      row({ employee: "Eligible, Eve", solo_hours: 4.75, premium_cents: 475 }),
      row({
        employee: "Already, Al",
        solo_hours: 6,
        base_rate_dollars: 16.25,
        eligible: false,
        premium_cents: 0,
      }),
    ]);
    expect(summary.premiumCents).toBe(475);
    expect(summary.people).toBe(2);
  });

  it("reports an empty Period as zero, not NaN", () => {
    const summary = summarizeSoloHours([]);
    expect(summary).toEqual({
      rows: [],
      soloHours: 0,
      premiumCents: 0,
      people: 0,
    });
  });

  it("tolerates null hours from a partially materialized row", () => {
    const summary = summarizeSoloHours([
      row({ solo_hours: null as unknown as number, premium_cents: null as unknown as number }),
    ]);
    expect(summary.soloHours).toBe(0);
    expect(summary.premiumCents).toBe(0);
  });
});
