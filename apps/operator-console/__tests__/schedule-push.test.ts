import { describe, expect, it } from "vitest";
import { inAdpSchedule, savedPlan, type PushRow, type PushShift } from "@/lib/labor/schedule-push";

const A: PushShift = { date: "2026-10-03", employee: "Doe, Alex", startMin: 600, endMin: 960 };
const OPEN: PushShift = { date: "2026-10-04", employee: null, startMin: 780, endMin: 1260 };

function row(s: PushShift, status: PushRow["status"], error: string | null = null): PushRow {
  return {
    push_id: "p",
    row_key: `palmetto|${s.date}|${s.employee ?? "OPEN"}|${s.startMin}|${s.endMin}`,
    date: s.date,
    employee: s.employee,
    start_min: s.startMin,
    end_min: s.endMin,
    status,
    error,
    updated_at: null,
  };
}

describe("savedPlan", () => {
  it("is every shift sent (landed or awaiting retry), minus skipped and deleted, in day order", () => {
    const later = { ...A, date: "2026-10-04", employee: "Roe, Sam" };
    const plan = savedPlan([
      row(later, "failed", "not attempted"),
      row(A, "drafted"),
      row({ ...A, startMin: 420 }, "deleted"),
      row(OPEN, "skipped"),
    ]);
    expect(plan).toEqual([{ ...A, status: "drafted" }, { ...later, status: "failed" }]);
  });
});

describe("inAdpSchedule", () => {
  const lanes = [
    { employee: "Johnson, Dolce J", open: false, segments: [{ startMin: 390, endMin: 870 }] },
    { employee: "", open: true, segments: [{ startMin: 900, endMin: 1230 }] },
  ];
  it("matches the same person (middle initial tolerated) and exact times", () => {
    expect(inAdpSchedule({ date: "2026-10-13", employee: "Johnson, Dolce", startMin: 390, endMin: 870 }, lanes)).toBe(true);
    expect(inAdpSchedule({ date: "2026-10-13", employee: "Johnson, Dolce", startMin: 390, endMin: 900 }, lanes)).toBe(false);
    expect(inAdpSchedule({ date: "2026-10-13", employee: "Huynh, Hillary", startMin: 390, endMin: 870 }, lanes)).toBe(false);
  });
  it("matches open shifts only against open lanes", () => {
    expect(inAdpSchedule({ date: "2026-10-13", employee: null, startMin: 900, endMin: 1230 }, lanes)).toBe(true);
    expect(inAdpSchedule({ date: "2026-10-13", employee: null, startMin: 390, endMin: 870 }, lanes)).toBe(false);
  });
});
