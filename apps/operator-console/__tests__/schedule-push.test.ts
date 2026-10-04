import { describe, expect, it } from "vitest";
import {
  pushRowKey,
  splitAgainstAdp,
  summarizePush,
  validatePushShifts,
  type PushRow,
  type PushShift,
} from "@/lib/labor/schedule-push";

const WEEK = "2026-09-28";
const A: PushShift = { date: "2026-10-03", employee: "Doe, Alex", startMin: 600, endMin: 960 };
const OPEN: PushShift = { date: "2026-10-04", employee: null, startMin: 780, endMin: 1260 };

function row(s: PushShift, status: PushRow["status"], error: string | null = null): PushRow {
  return {
    push_id: "p",
    row_key: pushRowKey("palmetto", s),
    date: s.date,
    employee: s.employee,
    start_min: s.startMin,
    end_min: s.endMin,
    status,
    error,
    updated_at: null,
  };
}

describe("validatePushShifts", () => {
  it("accepts in-week future shifts and normalizes blank employees to open", () => {
    const out = validatePushShifts(WEEK, [A, { ...OPEN, employee: "  " }], "2026-09-27");
    expect(out).toEqual([A, OPEN]);
  });

  it("rejects a non-Monday week, empty lists, out-of-week, past days and bad times", () => {
    expect(() => validatePushShifts("2026-09-29", [A], "2026-09-27")).toThrow(/Monday/);
    expect(() => validatePushShifts(WEEK, [], "2026-09-27")).toThrow(/No draft shifts/);
    expect(() => validatePushShifts(WEEK, [{ ...A, date: "2026-10-05" }], "2026-09-27")).toThrow(/outside the week/);
    expect(() => validatePushShifts(WEEK, [A], "2026-10-04")).toThrow(/in the past/);
    expect(() => validatePushShifts(WEEK, [A], "2026-10-03")).toThrow(/today/);
    expect(() => validatePushShifts(WEEK, [{ ...A, endMin: 600 }], "2026-09-27")).toThrow(/start\/end/);
  });
});

describe("splitAgainstAdp", () => {
  it("resends failed shifts and leaves drafted, skipped, queued and published alone", () => {
    const B = { ...A, employee: "Roe, Sam" };
    const C = { ...A, startMin: 420 };
    const { toSave, inAdp } = splitAgainstAdp(
      [A, B, C, OPEN],
      [row(A, "drafted"), row(B, "failed", "not listed"), row(OPEN, "skipped")],
    );
    expect(toSave).toEqual([B, C]);
    expect(inAdp).toEqual([A, OPEN]);
  });
});

describe("summarizePush", () => {
  it("counts statuses and collects errors", () => {
    const s = summarizePush([row(A, "drafted"), row(OPEN, "failed", "boom")]);
    expect(s.drafted).toBe(1);
    expect(s.failed).toBe(1);
    expect(s.errors).toEqual(["boom"]);
  });
});
