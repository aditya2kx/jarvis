// @vitest-environment jsdom
// Issue #512: the staffing minimum is always drafted; order-driven shifts stop at the labor % cap.
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));
vi.mock("@/app/labor/actions", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return Object.fromEntries(
    Object.keys(actual).map((k) => [k, vi.fn().mockResolvedValue({ ok: true, data: { running: null } })]),
  );
});
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));
import { cleanup, render, screen } from "@testing-library/react";
import { LaborCoveragePanel } from "@/components/labor/LaborCoveragePanel";
import { shiftCalendarDate } from "@/lib/filters/range";
import type { TrendDay } from "@/lib/labor/demand-trend";
import { DEFAULT_RULES, type RulesVersion, type ScheduleRules } from "@/lib/labor/schedule-inputs";
import type { PushRow } from "@/lib/labor/schedule-push";
import type { DemandCell } from "@/lib/labor/staffing-need";

afterEach(cleanup);
// jsdom has no layout scrolling; the day strip centres the selected chip on mount.
Element.prototype.scrollTo = () => {};

const TODAY = "2026-11-15";
const WEEK = "2026-11-16";
const WIN = { start: WEEK, end: "2026-11-22", label: "Next week", preset: "custom" as const };
const STAFF = ["A", "B", "C", "D", "E", "F", "G", "H"].map((x) => `Staff, ${x}`);

// 4 orders every open hour (two on the floor with the solo rule), 12 at noon (a 3rd person at 5/person).
const DEMAND: DemandCell[] = Array.from({ length: 7 }, (_, dow) =>
  Array.from({ length: 15 }, (_, i) => ({ dow, hour: 6 + i, orders: 6 + i === 12 ? 12 : 4 })),
).flat();

/** 8 flat weeks: $1,000 and 100 orders a day; labor $ per paid hour = cost ÷ 30. */
const trend = (cost: number): TrendDay[] =>
  Array.from({ length: 56 }, (_, i) => ({
    date: shiftCalendarDate(TODAY, "day", -56 + i),
    orders: 100,
    net_sales: 1000,
    labor_cost: cost,
    hours: 30,
  }));

const rules = (laborCapPct: number | null): ScheduleRules => ({
  ...DEFAULT_RULES,
  dayRules: [],
  staffing: {
    ...DEFAULT_RULES.staffing,
    ordersPerPerson: 5,
    soloMaxOrders: 2,
    soloMinMin: 60,
    laborCapPct,
    trendWeights: [0.4, 0.3, 0.2, 0.1],
    minShiftMin: 240,
    maxShiftMin: 540,
    shiftTimes: "need",
    unpaidMealMin: 0,
  },
});

const history = (r: ScheduleRules): RulesVersion[] => [
  { version: 30, rules: r, savedStaffing: true, note: null, createdBy: "adi@mypalmetto.co", createdAt: "2026-11-14T12:00:00Z" },
];

function draftText(opts: { cap: number | null; cost?: number; goal?: number; pushRows?: PushRow[] }) {
  render(
    <LaborCoveragePanel
      store="palmetto"
      win={WIN}
      actuals={[]}
      scheduled={[]}
      laborTypes={null}
      todayIso={TODAY}
      demand={DEMAND}
      trendDays={trend(opts.cost ?? 400)}
      goalHoursWeek={opts.goal}
      rulesHistory={history(rules(opts.cap))}
      activeStaff={STAFF}
      savedPushRows={opts.pushRows}
    />,
  );
  return screen.getByTestId("shift-draft").textContent ?? "";
}

const totalHours = (text: string) => Number(/=\s*(\d+)h/.exec(text)?.[1]);

describe("labor % cap in the shift draft", () => {
  // Floor: 6:30 AM–8:30 PM, two all day (4 orders/hour > 2) = 28 person-hours × 7 = 196h.
  it("drafts the staffing minimum even when it alone is over the cap, and says so", () => {
    // cap = 40% × $7,000 ÷ ($400 / 30h) = 210h → at $16/h (cost 480) it is 175h, under the floor.
    const text = draftText({ cap: 0.4, cost: 480 });
    expect(text).toContain("175h cap · 40% of $7.0k forecast sales");
    expect(totalHours(text)).toBeGreaterThanOrEqual(196);
    expect(text).toMatch(/Over the cap: the staffing minimum needs these shifts/);
  });

  it("stops order-driven shifts at the cap", () => {
    const text = draftText({ cap: 0.4 });
    expect(text).toContain("210h cap · 40% of $7.0k forecast sales");
    const total = totalHours(text);
    expect(total).toBeGreaterThan(196);
    expect(total).toBeLessThanOrEqual(210);
    expect(text).toMatch(/Cap reached — [\d.]+h of peak-order demand left uncovered/);
  });

  it("covers every noon peak when the cap allows it", () => {
    const text = draftText({ cap: 1 });
    expect(totalHours(text)).toBeGreaterThanOrEqual(196 + 7 * 4);
    expect(text).not.toMatch(/reached/);
  });

  it("keeps the weekly hours goal when no labor cap is set (older rule versions)", () => {
    const text = draftText({ cap: null, goal: 300 });
    expect(text).toContain("of 300h goal");
    expect(text).not.toContain("forecast sales");
  });

  it("leaves a week already saved to ADP exactly as saved", () => {
    const pushRows: PushRow[] = [
      {
        push_id: "p1",
        row_key: `${WIN.end}|1`,
        date: WIN.end,
        employee: "Staff, A",
        start_min: 390,
        end_min: 870,
        status: "published",
        error: null,
        updated_at: "2026-11-14T12:00:00Z",
      },
    ];
    const text = draftText({ cap: 0.4, pushRows });
    expect(text).toContain("Locked — saved to ADP");
    expect(text).not.toMatch(/Cap reached|Over the cap/);
  });
});
