// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { DraftShift } from "@/lib/labor/shift-draft";

const assign = vi.fn();
const status = vi.fn();
const refresh = vi.fn();
vi.mock("@/app/labor/actions", () => ({
  assignOpenShiftsAction: (items: unknown) => assign(items),
  openShiftAssignStatusAction: (id: string | null) => status(id),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("@/lib/actions/ActionToast", () => ({ useActionToast: () => ({ push: vi.fn() }) }));

import {
  OpenShiftAssignBar,
  OpenShiftDayList,
  type AssignSelection,
  type DayOpenShift,
} from "@/components/labor/OpenShiftAssign";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

const fill = (slot: string, employee: string): DraftShift => ({
  date: "2026-10-27",
  kind: "close",
  startMin: 990,
  endMin: 1230,
  hours: 4,
  employee,
  trimmed: false,
  fillsOpen: true,
  slot,
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

function list(selected: string[] = [], enabled = true) {
  const onToggle = vi.fn();
  const onPick = vi.fn();
  render(
    <OpenShiftDayList
      dayLabel="Tue Oct 27"
      shifts={SHIFTS}
      selected={new Set(selected)}
      enabled={enabled}
      onToggle={onToggle}
      onPick={onPick}
    />,
  );
  return { onToggle, onPick };
}

describe("OpenShiftDayList", () => {
  it("lists every open shift with who takes it, and ticks one", () => {
    const { onToggle } = list();
    expect(screen.getAllByRole("combobox")).toHaveLength(2);
    expect(screen.getByText(/Nobody fits your rules/)).toBeTruthy();
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
    const again = list([A, B]);
    fireEvent.click(screen.getByRole("button", { name: "Clear this day" }));
    expect(again.onToggle).toHaveBeenCalledWith([A, B], false);
  });

  it("changing the person goes to the week's picks", async () => {
    const { onPick } = list([A]);
    fireEvent.click(screen.getAllByRole("combobox")[0]!);
    const opt = (await screen.findAllByRole("option")).find((o) => o.textContent?.includes("Huynh"))!;
    opt.focus();
    fireEvent.keyDown(opt, { key: "Enter" });
    await waitFor(() => expect(onPick).toHaveBeenCalledWith(A, "Huynh, Hillary"));
  });

  it("can't select when ADP writes are off", () => {
    list([], false);
    expect((screen.getAllByRole("checkbox") as HTMLInputElement[]).every((b) => b.disabled)).toBe(true);
    expect(screen.queryByRole("button", { name: "Select all" })).toBeNull();
    expect(screen.getByText(/ADP schedule writes are off/)).toBeTruthy();
  });
});

const ITEMS: AssignSelection[] = [
  { slot: A, date: "2026-10-27", startMin: 990, endMin: 1230, employee: "Denton, Cimino R" },
  { slot: "2026-10-31|adp|420|720", date: "2026-10-31", startMin: 420, endMin: 720, employee: "Huynh, Hillary" },
];

function bar(props: Partial<Parameters<typeof OpenShiftAssignBar>[0]> = {}) {
  status.mockResolvedValue({ ok: true, data: { rows: [], running: null } });
  const onClear = vi.fn();
  const onAssigned = vi.fn();
  render(<OpenShiftAssignBar items={ITEMS} dropped={0} enabled onClear={onClear} onAssigned={onAssigned} {...props} />);
  return { onClear, onAssigned };
}

describe("OpenShiftAssignBar", () => {
  it("counts the selection across days and clears it", () => {
    const { onClear } = bar();
    expect(screen.getByTestId("open-shift-assign-bar").textContent).toMatch(/2 open shifts selected · Tue, Oct 27, Sat, Oct 31/);
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onClear).toHaveBeenCalled();
  });

  it("says when selected shifts were dropped because ADP no longer lists them", () => {
    bar({ items: [], dropped: 1 });
    expect(screen.getByText(/1 selected shift is no longer open in ADP/)).toBeTruthy();
  });

  it("is hidden with nothing selected", () => {
    bar({ items: [] });
    expect(screen.queryByTestId("open-shift-assign-bar")).toBeNull();
  });

  it("Write to ADP is off when ADP writes are disabled", () => {
    bar({ enabled: false });
    expect((screen.getByRole("button", { name: "Write to ADP" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("reviews in a drawer, assigns, then hands back the assigned slots and reloads", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { onAssigned } = bar();
    assign.mockResolvedValue({ ok: true, data: { assignId: "a1" }, queued: ["assign"] });
    fireEvent.click(screen.getByRole("button", { name: "Write to ADP" }));
    expect(await screen.findByText("Assign 2 open shifts in ADP")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Assign 2 in ADP" }));
    await waitFor(() => expect(assign).toHaveBeenCalled());
    expect(assign.mock.calls[0]![0]).toEqual(ITEMS.map(({ date, startMin, endMin, employee }) => ({ date, startMin, endMin, employee })));

    const row = (s: AssignSelection, st: string, error: string | null = null) => ({
      assign_id: "a1",
      row_key: "k",
      date: s.date,
      employee: s.employee,
      start_min: s.startMin,
      end_min: s.endMin,
      status: st,
      error,
      adp_name: null,
      updated_at: null,
    });
    status.mockResolvedValue({
      ok: true,
      data: { rows: [row(ITEMS[0]!, "assigned"), row(ITEMS[1]!, "failed", "not eligible")], running: null },
    });
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await waitFor(() => expect(onAssigned).toHaveBeenCalledWith([A]));
    expect(refresh).toHaveBeenCalled();
    expect(screen.getByText("1 assigned in ADP")).toBeTruthy();
    expect(screen.getByText("Huynh, Hillary 2026-10-31: not eligible")).toBeTruthy();
  });
});
