// @vitest-environment jsdom
// Guards for the console design principles in docs/contributing/ui-polish.md (D2, D3, D11, D12).
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("@/app/labor/actions", () => ({
  schedulePushStatusAction: vi.fn().mockResolvedValue({ ok: true, data: { running: null } }),
  sendPublishMessageAction: vi.fn(),
}));
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { PersonLaneTrack, PersonName } from "@/components/labor/LaborCoveragePanel";
import { EntrySheet, type SheetTarget } from "@/components/labor/availability/EntrySheet";
import { AdpScheduleFinalize } from "@/components/labor/AdpScheduleFinalize";
import type { CoveragePersonDay } from "@/lib/labor/coverage-model";
import type { UnavailabilityRow } from "@/lib/bq/queries";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const TODAY = "2026-10-28";
const PERSON: CoveragePersonDay = {
  employee: "Lee, Ana",
  labor_bucket: "PT",
  segments: [
    { kind: "actual", startMin: 360, endMin: 420, hours: 1 },
    { kind: "scheduled", startMin: 420, endMin: 780, hours: 6 },
  ],
};
const OPEN: CoveragePersonDay = { employee: "Open shift", labor_bucket: "", segments: [{ kind: "open", startMin: 840, endMin: 1200, hours: 6 }] };
const ROW: UnavailabilityRow = {
  row_key: "adp|1",
  employee: "Lee, Ana",
  status: "approved",
  first_date: "2026-10-30",
  from_time: "18:00:00",
  to_time: "21:00:00",
  all_day: false,
  repeat_weekday: null,
  repeat_until: null,
  expires_at_ct: null,
  hours_left: null,
  scraped_at: null,
};
const SHIFTS = [{ date: "2026-10-30", employee: "Lee, Ana", shift_ranges_json: JSON.stringify(["7:00 AM - 1:00 PM"]) }];

function sheet(target: SheetTarget) {
  render(
    <EntrySheet
      target={target}
      roster={["Bo, Kim", "Lee, Ana"]}
      todayIso={TODAY}
      shifts={SHIFTS}
      offRows={[ROW]}
      canAvailability
      stagedShift={() => undefined}
      onTarget={vi.fn()}
      onClose={vi.fn()}
      onStage={vi.fn()}
      onStageShift={vi.fn()}
    />,
  );
}

const opens = (el: Element) => el.querySelector('[data-affordance="opens"]') != null;

describe("D2: one way to send shifts to ADP — the Changes to ADP tray's Apply", () => {
  it("the suggested week only adds to the tray; it has no save, publish or apply of its own", () => {
    const add = { kind: "shift-add" as const, date: "2026-10-30", employee: "Lee, Ana", startMin: 420, endMin: 780 };
    render(<AdpScheduleFinalize weekStart="2026-10-26" weeks={[{ weekStart: "2026-10-26", changes: [add] }]} onStage={vi.fn()} enabled />);
    const writes = screen.getAllByRole("button").filter((b) => /save|publish|apply|draft/i.test(b.textContent ?? ""));
    expect(writes.map((b) => b.textContent)).toEqual([]);
  });
});

describe("D3: the chart is for reading — a tap anywhere on a lane shows who's on", () => {
  it("lanes hold no buttons, and a tap on a shift reaches the timeline (tooltip pins)", () => {
    const timeline = vi.fn();
    const { container } = render(
      <div onPointerDown={timeline}>
        <PersonLaneTrack person={PERSON} axisStart={360} axisSpan={900} blocks={[{ row: ROW, fromMin: 1080, toMin: 1260 }]} />
        <PersonLaneTrack person={OPEN} axisStart={360} axisSpan={900} />
      </div>,
    );
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    for (const bar of container.querySelectorAll("[data-lane] > *")) fireEvent.pointerDown(bar);
    expect(timeline).toHaveBeenCalledTimes(container.querySelectorAll("[data-lane] > *").length);
  });
});

describe("D11: what opens something looks like it does", () => {
  it("a name on the chart carries a chevron", () => {
    render(<PersonName name="Lee, Ana" onPerson={vi.fn()} />);
    expect(opens(screen.getByRole("button", { name: /Lee, Ana/ }))).toBe(true);
  });

  it("every row in the person drawer that opens something carries a chevron", () => {
    sheet({ mode: "person", employee: "Lee, Ana" });
    const rows = [...screen.getByRole("dialog").querySelectorAll("section button:not(:disabled)")].filter(
      (b) => !/^Show all/.test(b.textContent ?? ""),
    );
    expect(rows.length).toBeGreaterThan(1);
    for (const b of rows) expect(opens(b), b.textContent ?? "").toBe(true);
  });
});

describe("D12: one screen per change — the sheet opens on its fields, not on a menu of actions", () => {
  it("a shift opens with its times and person ready to edit", () => {
    sheet({ mode: "shift", employee: "Lee, Ana", shift: { date: "2026-10-30", startMin: 420, endMin: 780, draft: false } });
    expect(screen.getByLabelText("Starts")).toBeTruthy();
    expect(screen.getByLabelText("Ends")).toBeTruthy();
    expect(screen.getByRole("combobox")).toBeTruthy();
    for (const step of ["Edit", "Edit times", "Reassign", "Assign"]) expect(screen.queryByRole("button", { name: step })).toBeNull();
  });

  it("an open shift opens with its times and who to assign", () => {
    sheet({ mode: "open-shift", slot: "s", date: "2026-10-30", startMin: 420, endMin: 780, options: ["Bo, Kim"], suggested: null });
    expect(screen.getByLabelText("Starts")).toBeTruthy();
    expect(screen.getByRole("combobox")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Assign" })).toBeNull();
  });

  it("approved unavailability opens on its hours, Save waits for a change", () => {
    sheet({ mode: "entry", row: ROW, date: "2026-10-30" });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    const dialog = screen.getByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Save to changes" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(within(dialog).getByRole("button", { name: "Delete" })).toBeTruthy();
  });
});
