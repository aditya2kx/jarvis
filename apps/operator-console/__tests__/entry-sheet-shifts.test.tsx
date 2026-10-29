// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  const SHIFT: SheetTarget = { mode: "shift", employee: "Lee, Ana", shift: { date: "2026-10-30", startMin: 420, endMin: 780, draft: false } };
  const base = { date: "2026-10-30", startMin: 420, endMin: 780 };
  const pick = async (name: string) => {
    fireEvent.click(screen.getByRole("combobox"));
    const opt = (await screen.findAllByRole("option")).find((o) => o.textContent?.includes(name))!;
    opt.focus();
    fireEvent.keyDown(opt, { key: "Enter" });
    await waitFor(() => expect(screen.getByRole("combobox").textContent).toContain(name));
  };
  const save = () => screen.getByRole("button", { name: "Save to changes" }) as HTMLButtonElement;

  it("a shift: Delete stages straight away", () => {
    const p = sheet(SHIFT);
    expect(screen.getByText("Published")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(p.onStageShift).toHaveBeenCalledWith({ kind: "shift-delete", employee: "Lee, Ana", ...base });
  });

  it("a shift: new times stage an edit; Save waits for a change", () => {
    const p = sheet(SHIFT);
    expect(save().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "08:00" } });
    fireEvent.click(save());
    expect(p.onStageShift).toHaveBeenCalledWith({ kind: "shift-edit", employee: "Lee, Ana", ...base, to: { startMin: 480, endMin: 780 } });
  });

  it("a shift: a new person stages a reassign and says ADP deletes + adds", async () => {
    const p = sheet(SHIFT);
    await pick("Bo, Kim");
    expect(screen.getByText(/deletes it and adds it for Bo, Kim/)).toBeTruthy();
    fireEvent.click(save());
    expect(p.onStageShift).toHaveBeenCalledWith({ kind: "shift-reassign", employee: "Lee, Ana", ...base, toEmployee: "Bo, Kim" });
  });

  it("a shift: new person and new times = delete + a new shift", async () => {
    const p = sheet(SHIFT);
    await pick("Bo, Kim");
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "14:00" } });
    fireEvent.click(save());
    expect(p.onStageShift.mock.calls.map((c) => c[0])).toEqual([
      { kind: "shift-delete", employee: "Lee, Ana", ...base },
      { kind: "shift-add", date: "2026-10-30", employee: "Bo, Kim", startMin: 420, endMin: 840 },
    ]);
  });

  const OPEN: SheetTarget = { mode: "open-shift", slot: "2026-10-30|adp|420|780", ...base, options: ["Bo, Kim"], suggested: "Bo, Kim" };

  it("an open shift: prefilled with the suggestion, Save assigns; Delete removes", () => {
    const p = sheet(OPEN);
    expect(screen.getByText("Unassigned")).toBeTruthy();
    fireEvent.click(save());
    expect(p.onStageShift).toHaveBeenCalledWith({ kind: "open-assign", slot: "2026-10-30|adp|420|780", ...base, employee: "Bo, Kim" });
    cleanup();
    const q = sheet(OPEN);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(q.onStageShift).toHaveBeenCalledWith({ kind: "open-delete", slot: "2026-10-30|adp|420|780", ...base });
  });

  it("an open shift with nobody picked: new times stage an edit; nobody fits → only times", () => {
    const p = sheet({ ...OPEN, suggested: null });
    expect(save().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "08:00" } });
    fireEvent.click(save());
    expect(p.onStageShift).toHaveBeenCalledWith({ kind: "open-edit", slot: "2026-10-30|adp|420|780", ...base, to: { startMin: 480, endMin: 780 } });
    cleanup();
    sheet({ ...OPEN, options: [], suggested: null });
    expect(screen.getByText("Nobody fits your rules for this shift.")).toBeTruthy();
  });

  it("today's shift can't be changed", () => {
    sheet({ ...SHIFT, shift: { ...SHIFT.shift, date: TODAY } } as SheetTarget);
    expect(screen.getByText(/can.t be changed from here/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
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
