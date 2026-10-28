// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { EntrySheet, type SheetTarget } from "@/components/labor/availability/EntrySheet";
import type { ShiftChange } from "@/lib/labor/adp-changes";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const TODAY = "2026-10-28";
const SHIFTS = [
  { date: "2026-10-30", employee: "Lee, Ana", shift_ranges_json: JSON.stringify(["7:00 AM - 1:00 PM"]) },
  { date: "2026-11-01", employee: "Lee, Ana", shift_ranges_json: JSON.stringify(["2:00 PM - 8:00 PM DRAFT"]) },
  { date: "2026-12-20", employee: "Lee, Ana", shift_ranges_json: JSON.stringify(["2:00 PM - 8:00 PM"]) },
];

function sheet(target: SheetTarget, opts: { canAvailability?: boolean; staged?: Record<string, ShiftChange> } = {}) {
  const p = { onTarget: vi.fn(), onClose: vi.fn(), onStage: vi.fn(), onStageShift: vi.fn() };
  render(
    <EntrySheet
      target={target}
      roster={["Bo, Kim", "Lee, Ana"]}
      todayIso={TODAY}
      shifts={SHIFTS}
      canAvailability={opts.canAvailability ?? true}
      stagedShift={(k) => opts.staged?.[k]}
      {...p}
    />,
  );
  return p;
}

describe("EntrySheet — shifts", () => {
  it("the person drawer lists the next 14 days of shifts (draft tagged), Show all for later", () => {
    const p = sheet({ mode: "person", employee: "Lee, Ana" });
    const section = screen.getByTestId("person-shifts");
    expect(section.textContent).toMatch(/Fri, Oct 30 · 7 AM–1 PM/);
    expect(section.textContent).toMatch(/Sun, Nov 1 · 2 PM–8 PM\s*Draft/);
    expect(section.textContent).not.toMatch(/Dec 20/);
    fireEvent.click(screen.getByRole("button", { name: "Show all 3" }));
    expect(screen.getByTestId("person-shifts").textContent).toMatch(/Dec 20/);
    fireEvent.click(screen.getByRole("button", { name: /Fri, Oct 30/ }));
    expect(p.onTarget).toHaveBeenCalledWith({
      mode: "shift",
      employee: "Lee, Ana",
      shift: { date: "2026-10-30", startMin: 420, endMin: 780, draft: false },
    });
  });

  it("shows a staged change on the shift row", () => {
    sheet(
      { mode: "person", employee: "Lee, Ana" },
      { staged: { "shift|Lee, Ana|2026-10-30|420-780": { kind: "shift-reassign", date: "2026-10-30", employee: "Lee, Ana", toEmployee: "Bo, Kim", startMin: 420, endMin: 780 } } },
    );
    expect(screen.getByText("Will go to Bo, Kim")).toBeTruthy();
  });

  it("without Availability access: Add shift only, no Add unavailability", () => {
    sheet({ mode: "person", employee: "Lee, Ana" }, { canAvailability: false });
    expect(screen.getByRole("button", { name: "Add shift" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add unavailability" })).toBeNull();
  });

  it("a shift: Delete stages straight away; Edit times stages the new times", () => {
    const target: SheetTarget = { mode: "shift", employee: "Lee, Ana", shift: { date: "2026-10-30", startMin: 420, endMin: 780, draft: false } };
    const p = sheet(target);
    expect(screen.getByText("Published")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(p.onStageShift).toHaveBeenCalledWith({ kind: "shift-delete", date: "2026-10-30", employee: "Lee, Ana", startMin: 420, endMin: 780 });
    cleanup();
    const q = sheet(target);
    fireEvent.click(screen.getByRole("button", { name: "Edit times" }));
    const save = screen.getByRole("button", { name: "Save to changes" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "08:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Save to changes" }));
    expect(q.onStageShift).toHaveBeenCalledWith({
      kind: "shift-edit",
      date: "2026-10-30",
      employee: "Lee, Ana",
      startMin: 420,
      endMin: 780,
      to: { startMin: 480, endMin: 780 },
    });
  });

  it("Reassign explains delete + new shift and needs a person", () => {
    sheet({ mode: "shift", employee: "Lee, Ana", shift: { date: "2026-10-30", startMin: 420, endMin: 780, draft: false } });
    fireEvent.click(screen.getByRole("button", { name: "Reassign" }));
    expect(screen.getByText(/deletes it and adds the same hours/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Reassign in changes" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("add open shift: many days at once with a count each", () => {
    const p = sheet({ mode: "open-add", date: "2026-10-30" });
    fireEvent.click(screen.getByRole("button", { name: /Sat, Oct 31/ }));
    fireEvent.change(screen.getByLabelText("How many each day"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Add 2 days to changes" }));
    expect(p.onStageShift.mock.calls.map((c) => c[0])).toEqual([
      { kind: "open-add", date: "2026-10-30", count: 2, startMin: 540, endMin: 900 },
      { kind: "open-add", date: "2026-10-31", count: 2, startMin: 540, endMin: 900 },
    ]);
    expect(p.onClose).toHaveBeenCalled();
  });

  it("today can't be picked for a new shift", () => {
    sheet({ mode: "shift-add", employee: "Lee, Ana", date: TODAY });
    expect((screen.getByRole("button", { name: /Wed, Oct 28/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
