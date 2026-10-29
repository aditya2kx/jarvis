// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DraftShift } from "@/lib/labor/shift-draft";
import type { ShiftChange } from "@/lib/labor/adp-changes";
import { OpenShiftDayList, type DayOpenShift } from "@/components/labor/OpenShiftAssign";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const fill = (slot: string, employee: string, picked = false): DraftShift => ({
  date: "2026-10-27",
  kind: "close",
  startMin: 990,
  endMin: 1230,
  hours: 4,
  employee,
  trimmed: false,
  fillsOpen: true,
  slot,
  picked,
  options: [
    { employee, startMin: 990, endMin: 1230, hours: 4, trimmed: false, weekHours: 8, cap: 30 },
    { employee: "Huynh, Hillary", startMin: 990, endMin: 1230, hours: 4, trimmed: false, weekHours: 2, cap: 20 },
  ],
});

const A = "2026-10-27|adp|990|1230";
const B = "2026-10-27|adp|990|1230|1";
const C = "2026-10-27|adp|420|720";
const SHIFTS: DayOpenShift[] = [
  { slot: A, date: "2026-10-27", startMin: 990, endMin: 1230, hours: 4, fill: fill(A, "Denton, Cimino R") },
  { slot: B, date: "2026-10-27", startMin: 990, endMin: 1230, hours: 4, fill: fill(B, "Orourke, Elizabeth") },
  { slot: C, date: "2026-10-27", startMin: 420, endMin: 720, hours: 5, fill: null },
];

function list({
  selected = [],
  shifts = SHIFTS,
  staged = {},
  showSuggestions = true,
  adds = [],
}: {
  selected?: string[];
  shifts?: DayOpenShift[];
  staged?: Record<string, ShiftChange>;
  showSuggestions?: boolean;
  adds?: Extract<ShiftChange, { kind: "open-add" }>[];
} = {}) {
  const props = {
    onToggle: vi.fn(),
    onPick: vi.fn(),
    onAdd: vi.fn(),
    onOpen: vi.fn(),
    onUndo: vi.fn(),
  };
  render(
    <OpenShiftDayList
      dayLabel="Tue Oct 27"
      shifts={shifts}
      adds={adds}
      selected={new Set(selected)}
      staged={(k) => staged[k]}
      showSuggestions={showSuggestions}
      {...props}
    />,
  );
  return props;
}

describe("OpenShiftDayList", () => {
  it("lists every open shift with who takes it, and ticks one", () => {
    const { onToggle } = list();
    expect(screen.getAllByRole("combobox")).toHaveLength(2);
    expect(screen.getByText("Nobody fits your rules")).toBeTruthy();
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.disabled)).toEqual([false, false, true]);
    fireEvent.click(boxes[0]!);
    expect(onToggle).toHaveBeenCalledWith([A], true);
  });

  it("selects all fillable shifts on the day, then clears them", () => {
    const { onToggle } = list();
    fireEvent.click(screen.getByRole("button", { name: "Select all" }));
    expect(onToggle).toHaveBeenCalledWith([A, B], true);
    cleanup();
    const again = list({ selected: [A, B] });
    fireEvent.click(screen.getByRole("button", { name: "Clear this day" }));
    expect(again.onToggle).toHaveBeenCalledWith([A, B], false);
  });

  it("changing the person goes to the week's picks", async () => {
    const { onPick } = list({ selected: [A] });
    fireEvent.click(screen.getAllByRole("combobox")[0]!);
    const opt = (await screen.findAllByRole("option")).find((o) => o.textContent?.includes("Huynh"))!;
    opt.focus();
    fireEvent.keyDown(opt, { key: "Enter" });
    await waitFor(() => expect(onPick).toHaveBeenCalledWith(A, "Huynh, Hillary"));
  });

  it("Change opens the open shift's sheet; Add open shift adds more", () => {
    const p = list();
    fireEvent.click(screen.getAllByRole("button", { name: "Change the 4:30 PM–8:30 PM open shift" })[0]!);
    expect(p.onOpen).toHaveBeenCalledWith(SHIFTS[0]);
    fireEvent.click(screen.getByRole("button", { name: "Change the 7 AM–12 PM open shift" }));
    expect(p.onOpen).toHaveBeenCalledWith(SHIFTS[2]);
    fireEvent.click(screen.getByRole("button", { name: /Add open shift/ }));
    expect(p.onAdd).toHaveBeenCalled();
  });

  it("shows a staged delete in the row with undo, and can't tick it", () => {
    const del: ShiftChange = { kind: "open-delete", slot: A, date: "2026-10-27", startMin: 990, endMin: 1230 };
    const p = list({ selected: [A], staged: { [`open|${A}`]: del } });
    expect(screen.getByText("Will delete")).toBeTruthy();
    expect((screen.getAllByRole("checkbox")[0] as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(p.onUndo).toHaveBeenCalledWith(`open|${A}`);
  });

  it("with suggestions off, asks who instead of naming the suggestion (a ticked shift keeps its person)", () => {
    list({ selected: [B], showSuggestions: false });
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.map((b) => b.disabled)).toEqual([true, false, true]);
    expect(screen.getByText("Choose who")).toBeTruthy();
    expect(screen.getByText("Orourke, Elizabeth")).toBeTruthy();
    expect(screen.queryByText("Denton, Cimino R")).toBeNull();
  });

  it("an empty future day still offers Add open shift", () => {
    const p = list({ shifts: [] });
    expect(screen.getByText(/No open shifts in ADP — Tue Oct 27/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Add open shift/ }));
    expect(p.onAdd).toHaveBeenCalled();
  });

  it("shows open shifts staged to be added on the day, with undo", () => {
    const add = { kind: "open-add" as const, date: "2026-10-27", startMin: 420, endMin: 780, count: 2 };
    const p = list({ shifts: [], adds: [add] });
    expect(screen.queryByText(/No open shifts in ADP/)).toBeNull();
    expect(screen.getByText("Open shifts — Tue Oct 27")).toBeTruthy();
    expect(screen.getByText("Will add 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Undo adding the 7 AM–1 PM open shift" }));
    expect(p.onUndo).toHaveBeenCalledWith("open-add|2026-10-27|420-780");
  });
});
