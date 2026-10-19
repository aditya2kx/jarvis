import { describe, expect, it } from "vitest";
import { OPEN_LANE_BUCKET, type CoveragePersonDay } from "@/lib/labor/coverage-model";
import { planShift } from "@/lib/labor/saved-plan-shift";

const MEAL = { afterMin: 360, unpaidMin: 30 };
const DATE = "2026-10-27";
const OPEN = { date: DATE, employee: null, startMin: 990, endMin: 1230, status: "published" as const };
const BERDING = { date: DATE, employee: "Berding, Kenya N", startMin: 780, endMin: 1230, status: "published" as const };

const lane = (employee: string, startMin: number, endMin: number, kind: "scheduled" | "open" = "scheduled") =>
  ({
    employee,
    labor_bucket: kind === "open" ? OPEN_LANE_BUCKET : "parttime",
    segments: [{ kind, startMin, endMin, hours: (endMin - startMin) / 60 }],
  }) as unknown as CoveragePersonDay;

describe("planShift for a saved open shift", () => {
  it("is in ADP while ADP still lists it open", () => {
    const s = planShift(OPEN, [lane("Open shift 1", 990, 1230, "open"), lane(BERDING.employee, 780, 1230)], MEAL, [OPEN, BERDING]);
    expect(s).toMatchObject({ inAdp: true, takenBy: undefined, employee: null });
  });

  it("is the taker's shift once someone takes it in ADP — never offered to save again", () => {
    const s = planShift(OPEN, [lane("Orourke, Elizabeth", 990, 1230), lane(BERDING.employee, 780, 1230)], MEAL, [OPEN, BERDING]);
    expect(s).toMatchObject({ inAdp: true, takenBy: "Orourke, Elizabeth", employee: null });
  });

  it("doesn't count a saved person's own same-time shift as the taker", () => {
    const own = { ...OPEN, employee: "Doe, Alex" };
    const s = planShift(OPEN, [lane("Doe, Alex", 990, 1230)], MEAL, [OPEN, own]);
    expect(s).toMatchObject({ inAdp: false, takenBy: undefined });
  });
});
