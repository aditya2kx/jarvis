// @vitest-environment jsdom
/** LaborCoveragePanel drops people who are not ADP-Active or past their last working day. */
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

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { LaborCoveragePanel } from "@/components/labor/LaborCoveragePanel";
import { DEFAULT_RULES, DEFAULT_TRAINING, type RulesVersion, type ScheduleRules } from "@/lib/labor/schedule-inputs";

afterEach(cleanup);
Element.prototype.scrollTo = () => {};

const TODAY = "2026-11-26";
const DAY = "2026-11-28";
const WIN = { start: DAY, end: DAY, label: "One day", preset: "custom" as const };

const RULES: ScheduleRules = {
  ...DEFAULT_RULES,
  dayRules: [],
  staffing: {
    ...DEFAULT_RULES.staffing,
    training: {
      ...DEFAULT_TRAINING,
      experienced: ["Guerrero, Amy", "Ortiz, Ximena", "Berding, Kenya N"],
    },
  },
  staffRules: [
    { id: "amy", employee: "Guerrero, Amy", kind: "target_week_hours", value: 30 },
    { id: "ximena-hours", employee: "Ortiz, Ximena", kind: "target_week_hours", value: 25 },
    { id: "ximena-last", employee: "Ortiz, Ximena", kind: "last_day", value: 0, date: "2026-11-20" },
    { id: "eileen", employee: "Rodriguez, Eileen", kind: "target_week_hours", value: 20 },
    { id: "kenya", employee: "Berding, Kenya N", kind: "last_day", value: 0, date: "2026-10-19" },
  ],
};

const HISTORY: RulesVersion[] = [
  {
    version: 34,
    rules: RULES,
    savedStaffing: true,
    note: "test",
    createdBy: "test",
    createdAt: "2026-11-26T12:00:00Z",
  },
];

const scheduled = (employee: string) => ({
  date: DAY,
  employee,
  labor_bucket: "parttime" as const,
  scheduled_hours: 6,
  shift_ranges_json: JSON.stringify(["9:00 AM - 3:00 PM"]),
});

describe("LaborCoveragePanel — active ADP staff only", () => {
  it("drops terminated and past-last-day people from staff rules and the free list; leftover ADP shifts stay tagged", () => {
    // Directory Active: Amy + Ximena (+ Padron with no shifts → free). Eileen/Kenya terminated.
    // Ximena's last day 2026-11-20 is past TODAY → not free / not drafted; her last_day rule stays.
    // Scheduled rows for inactive people stay on the timeline so you can delete them, tagged "not active".
    render(
      <LaborCoveragePanel
        store="palmetto"
        win={WIN}
        actuals={[]}
        scheduled={[
          scheduled("Guerrero, Amy"),
          scheduled("Ortiz, Ximena"),
          scheduled("Rodriguez, Eileen"),
          scheduled("Berding, Kenya N"),
        ]}
        laborTypes={null}
        todayIso={TODAY}
        demand={[{ dow: 5, hour: 9, orders: 4 }]}
        rulesHistory={HISTORY}
        activeStaff={["Guerrero, Amy", "Ortiz, Ximena", "Padron, Lisette"]}
      />,
    );

    // Ximena (past last day) + Eileen + Kenya still on the ADP schedule → tagged so you can delete them.
    expect(screen.getAllByText("not active in ADP").length).toBeGreaterThanOrEqual(2);

    const rulesToggle = screen.getByTestId("schedule-inputs").querySelector("button[aria-expanded]")!;
    fireEvent.click(rulesToggle);
    const people = screen.getAllByTestId("staff-rules-person").map((el) => within(el).getByText(/,/).textContent);
    expect(people).toEqual(["Guerrero, Amy", "Ortiz, Ximena"]);
    expect(screen.getByText("Last working day")).toBeTruthy();
    const ruleBlock = screen.getByTestId("schedule-inputs");
    expect(within(ruleBlock).queryByText("Rodriguez, Eileen")).toBeNull();
    expect(within(ruleBlock).queryByText("Berding, Kenya N")).toBeNull();
  });
});
