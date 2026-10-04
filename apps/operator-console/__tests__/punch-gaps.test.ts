import { describe, expect, it } from "vitest";
import {
  buildPunchGaps,
  filterPunchGaps,
  formatClock,
  hoursIfClosedAt,
  punchGapOptions,
  sortPunchGaps,
  writableToAdp,
  isOpenGap,
  decisionLabel,
  decisionTimes,
  summarizePunchGaps,
  timelineBounds,
  validateDecision,
  type PunchGapRow,
} from "@/lib/labor/punch-gaps";

function row(over: Partial<PunchGapRow> = {}): PunchGapRow {
  return {
    date: "2026-09-30",
    employee_id: "Doe, Jane",
    kind: "missing_out_after_break",
    entries_json: JSON.stringify([
      { in: "09:10", out: "09:25", hours: 0.25 },
      { in: "11:10", out: null, hours: 0 },
    ]),
    scheduled_ranges_json: JSON.stringify([["11:30", "13:30"], ["13:30", "20:30"]]),
    open_entry_index: 1,
    suggested_out: "20:30",
    suggested_hours: 9.33,
    rule: "close_at_scheduled_end",
    adp_error_flag: false,
    decision_id: null,
    decision_action: null,
    decision_in_time: null,
    decision_out_time: null,
    decision_status: null,
    decision_error: null,
    decided_by: null,
    decided_at: null,
    ...over,
  };
}

const coworkers = [
  { date: "2026-09-30", employee: "Doe, Jane", in_time: "09:10", out_time: "09:25" },
  { date: "2026-09-30", employee: "Roe, Sam", in_time: "15:57", out_time: "20:03" },
  { date: "2026-09-29", employee: "Poe, Al", in_time: "10:00", out_time: "18:00" },
];

describe("buildPunchGaps", () => {
  it("parses JSON columns and keeps only same-day coworkers, excluding the employee", () => {
    const [gap] = buildPunchGaps([row()], coworkers);
    expect(gap!.entries[1]).toEqual({ in: "11:10", out: null, hours: 0 });
    expect(gap!.scheduled).toEqual([["11:30", "13:30"], ["13:30", "20:30"]]);
    expect(gap!.coworkers.map((c) => c.employee)).toEqual(["Roe, Sam"]);
    expect(gap!.decision).toBeNull();
  });

  it("carries the latest decision and sorts newest day first", () => {
    const gaps = buildPunchGaps(
      [
        row({ date: "2026-09-21", employee_id: "Ash, Bo" }),
        row({ decision_action: "accept", decision_out_time: "20:30", decision_status: "recorded" }),
      ],
      [],
    );
    expect(gaps.map((g) => g.date)).toEqual(["2026-09-30", "2026-09-21"]);
    expect(gaps[0]!.decision).toMatchObject({ action: "accept", outTime: "20:30", status: "recorded" });
  });

  it("survives malformed JSON instead of crashing the page", () => {
    const [gap] = buildPunchGaps([row({ entries_json: "{", scheduled_ranges_json: null })], []);
    expect(gap!.entries).toEqual([]);
    expect(gap!.scheduled).toEqual([]);
  });
});

describe("validateDecision", () => {
  const [gap] = buildPunchGaps([row()], []);

  it("accepts the suggestion and any later edit", () => {
    expect(validateDecision(gap!, { action: "accept" })).toBeNull();
    expect(validateDecision(gap!, { action: "edit", outTime: "19:00" })).toBeNull();
    expect(validateDecision(gap!, { action: "reject" })).toBeNull();
  });

  it("refuses an Out at or before the open clock-in", () => {
    expect(validateDecision(gap!, { action: "edit", outTime: "11:10" })).toMatch(/after the 11:10 AM/);
    expect(validateDecision(gap!, { action: "edit", outTime: "" })).toMatch(/Enter an Out/);
  });

  it("no_entry accepts the scheduled shift when suggested, else needs both times", () => {
    const [sug] = buildPunchGaps(
      [row({ kind: "no_entry", entries_json: "[]", open_entry_index: null, suggested_in: "11:30", suggested_out: "20:30" })],
      [],
    );
    expect(sug!.suggestedIn).toBe("11:30");
    expect(validateDecision(sug!, { action: "accept" })).toBeNull();
    const [ne] = buildPunchGaps(
      [row({ kind: "no_entry", entries_json: "[]", open_entry_index: null, suggested_out: null })],
      [],
    );
    expect(validateDecision(ne!, { action: "accept" })).toMatch(/No suggestion/);
    expect(validateDecision(ne!, { action: "edit", inTime: "13:30", outTime: null })).toMatch(/both/);
    expect(validateDecision(ne!, { action: "edit", inTime: "20:30", outTime: "13:30" })).toMatch(/after In/);
    expect(validateDecision(ne!, { action: "edit", inTime: "13:30", outTime: "20:30" })).toBeNull();
  });

  it("in-progress and missing-in rows cannot be closed from the console", () => {
    const [ip] = buildPunchGaps([row({ kind: "in_progress" })], []);
    const [mi] = buildPunchGaps([row({ kind: "missing_in" })], []);
    expect(validateDecision(ip!, { action: "accept" })).toMatch(/Still on shift/);
    expect(validateDecision(mi!, { action: "edit", outTime: "20:00" })).toMatch(/review-only/);
    expect(validateDecision(mi!, { action: "reject" })).toBeNull();
  });
});

describe("helpers", () => {
  it("formats clock times the way ADP shows them", () => {
    expect(formatClock("20:30")).toBe("8:30 PM");
    expect(formatClock("00:05")).toBe("12:05 AM");
    expect(formatClock("12:00")).toBe("12:00 PM");
    expect(formatClock(null)).toBe("—");
  });

  it("computes hours an Out would add", () => {
    expect(hoursIfClosedAt("11:10", "20:30")).toBe(9.33);
    expect(hoursIfClosedAt("11:10", "11:10")).toBeNull();
  });

  it("summarizes undecided actionable gaps (in-progress excluded)", () => {
    const gaps = buildPunchGaps(
      [
        row(),
        row({ employee_id: "Roe, Sam", suggested_hours: 6 }),
        row({ employee_id: "Poe, Al", kind: "in_progress", suggested_hours: null }),
        row({ employee_id: "Ash, Bo", decision_action: "reject", decision_status: "rejected" }),
      ],
      [],
    );
    expect(summarizePunchGaps(gaps)).toEqual({ total: 3, undecided: 2, suggestedHours: 15.33 });
  });

  it("pads the shared timeline axis to whole hours", () => {
    const gaps = buildPunchGaps([row()], []);
    expect(timelineBounds(gaps)).toEqual({ start: 9 * 60, end: 21 * 60 });
    expect(timelineBounds([])).toEqual({ start: 360, end: 1320 });
  });
});

describe("table sort + filters", () => {
  const gaps = buildPunchGaps(
    [
      row({ date: "2026-09-30", employee_id: "Roe, Sam", suggested_hours: 2 }),
      row({ date: "2026-09-21", employee_id: "Ash, Bo", suggested_hours: 5 }),
      row({ date: "2026-09-25", employee_id: "Ash, Bo", kind: "no_entry", suggested_hours: null }),
      row({
        date: "2026-09-30",
        employee_id: "Ash, Bo",
        decision_action: "reject",
        decision_status: "recorded",
        suggested_hours: 1,
      }),
    ],
    [],
  );
  const ids = (gs: typeof gaps) => gs.map((g) => `${g.date} ${g.employee}`);

  it("sorts by day (ties by employee) and by employee (ties by day)", () => {
    expect(ids(sortPunchGaps(gaps, { column: "day", desc: true }))).toEqual([
      "2026-09-30 Ash, Bo",
      "2026-09-30 Roe, Sam",
      "2026-09-25 Ash, Bo",
      "2026-09-21 Ash, Bo",
    ]);
    expect(ids(sortPunchGaps(gaps, { column: "employee", desc: false }))).toEqual([
      "2026-09-21 Ash, Bo",
      "2026-09-25 Ash, Bo",
      "2026-09-30 Ash, Bo",
      "2026-09-30 Roe, Sam",
    ]);
    expect(ids(sortPunchGaps(gaps, { column: "suggestion", desc: true }))[0]).toBe("2026-09-21 Ash, Bo");
  });

  it("multi-select filters AND across columns, OR within one, with faceted options", () => {
    const filters = { employee: ["Ash, Bo"], decision: ["Needs decision"] };
    expect(ids(filterPunchGaps(gaps, filters)).sort()).toEqual([
      "2026-09-21 Ash, Bo",
      "2026-09-25 Ash, Bo",
    ]);
    expect(filterPunchGaps(gaps, { day: ["2026-09-21", "2026-09-25"] })).toHaveLength(2);
    expect(punchGapOptions(gaps, { employee: ["Roe, Sam"] }, "day")).toEqual(["2026-09-30"]);
    expect(punchGapOptions(gaps, filters, "employee")).toEqual(["Ash, Bo", "Roe, Sam"]);
    expect(punchGapOptions(gaps, { employee: ["Roe, Sam"] }, "decision")).toEqual(["Needs decision"]);
  });
});

describe("writableToAdp", () => {
  const decided = (status: string, over: Partial<PunchGapRow> = {}) =>
    row({
      decision_id: `id-${status}`,
      decision_action: "accept",
      decision_out_time: "20:30",
      decision_status: status as PunchGapRow["decision_status"],
      ...over,
    });

  it("sends accepted/edited clock-outs and new entries not in ADP yet or failed", () => {
    const gaps = buildPunchGaps(
      [
        decided("recorded", { employee_id: "A" }),
        decided("failed", { employee_id: "B" }),
        decided("applying", { employee_id: "C" }),
        decided("applied", { employee_id: "D" }),
        decided("rejected", { employee_id: "E", decision_action: "reject" }),
        decided("recorded", { employee_id: "F", kind: "no_entry", decision_in_time: "11:30" }),
        decided("recorded", { employee_id: "H", kind: "in_progress" }),
        row({ employee_id: "G" }),
      ],
      [],
    );
    expect(writableToAdp(gaps).map((g) => g.employee).sort()).toEqual(["A", "B", "F"]);
  });
});

describe("open view", () => {
  it("keeps accepted rows until ADP has the punch; dismissed rows leave", () => {
    const gaps = buildPunchGaps(
      ["recorded", "failed", "applying", "applied", "already_resolved"].map((status, i) =>
        row({
          employee_id: status,
          decision_id: `d${i}`,
          decision_action: "accept",
          decision_out_time: "20:30",
          decision_status: status as PunchGapRow["decision_status"],
        }),
      ).concat([
        row({ employee_id: "dismissed", decision_action: "reject", decision_status: "rejected" }),
        row({ employee_id: "new" }),
      ]),
      [],
    );
    expect(gaps.filter(isOpenGap).map((g) => g.employee).sort()).toEqual([
      "applying", "failed", "new", "recorded",
    ]);
    const label = (e: string) => decisionLabel(gaps.find((g) => g.employee === e)!);
    expect(label("recorded")).toBe("Accepted");
    expect(label("applied")).toBe("Written to ADP");
    expect(label("dismissed")).toBe("Dismissed");
  });
});

describe("decisionTimes", () => {
  it("shows a range for a new entry and the clock-out otherwise", () => {
    expect(decisionTimes({ inTime: "11:30", outTime: "20:30" })).toBe("11:30 AM – 8:30 PM");
    expect(decisionTimes({ inTime: null, outTime: "20:30" })).toBe("out 8:30 PM");
  });
});
