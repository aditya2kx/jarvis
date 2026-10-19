// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ShiftDraftSummary } from "@/components/labor/ShiftDraftSummary";
import type { DraftShift } from "@/lib/labor/shift-draft";

afterEach(cleanup);

const shift: DraftShift = {
  date: "2026-10-27",
  kind: "open",
  startMin: 390,
  endMin: 840,
  hours: 7,
  employee: "Doe, Alex",
  trimmed: false,
  slot: "2026-10-27|f|390|0",
  options: [
    { employee: "Doe, Alex", startMin: 390, endMin: 840, hours: 7, trimmed: false, weekHours: 12, cap: 30 },
    { employee: "Roe, Sam", startMin: 390, endMin: 720, hours: 5.5, trimmed: true, weekHours: 4, cap: 20, target: 20 },
    { employee: "Poe, Kim", startMin: 390, endMin: 840, hours: 7, trimmed: false, weekHours: 0, cap: 40 },
    { employee: null, startMin: 390, endMin: 840, hours: 7, trimmed: false },
  ],
};

function setup(props: Partial<Parameters<typeof ShiftDraftSummary>[0]> = {}) {
  const onPick = vi.fn();
  const onResetPicks = vi.fn();
  render(
    <ShiftDraftSummary
      dayLabel="Tue Oct 27"
      shifts={[shift]}
      roster={[]}
      weekStart="2026-10-26"
      existingHours={100}
      draftHours={7}
      draftCount={1}
      peakLeftHours={0}
      onPick={onPick}
      onResetPicks={onResetPicks}
      {...props}
    />,
  );
  return { onPick, onResetPicks };
}

describe("ShiftDraftSummary person picker", () => {
  it("shows the suggestion with a count of other options", () => {
    setup();
    const trigger = screen.getByRole("combobox", { name: /who takes the 6:30 AM–2 PM shift/i });
    expect(trigger.textContent).toContain("Doe, Alex");
    expect(trigger.textContent).toContain("3 other options");
  });

  it("lists every option with its week so far, and picks one", async () => {
    const { onPick } = setup();
    fireEvent.click(screen.getByRole("combobox"));
    const list = await screen.findByRole("listbox");
    const opts = within(list).getAllByRole("option");
    expect(opts.map((o) => o.textContent)).toEqual([
      expect.stringContaining("Doe, AlexSuggested12h of 30h this week"),
      expect.stringContaining("Roe, Sam6:30 AM–12 PM · 5.5h · 4h of 20h target this week · shortened to availability"),
      expect.stringContaining("Poe, Kim0h of 40h this week"),
      expect.stringContaining("Leave openPosts as an open shift"),
    ]);
    opts[1]!.focus();
    fireEvent.keyDown(opts[1]!, { key: "Enter" });
    await waitFor(() => expect(onPick).toHaveBeenCalledWith(shift.slot, "Roe, Sam"));
  });

  it("marks a pick and resets it back to the suggestion", () => {
    const { onPick, onResetPicks } = setup({
      shifts: [{ ...shift, employee: "Roe, Sam", picked: true }],
      pickCount: 1,
      droppedPicks: 1,
    });
    expect(screen.getByText("Your pick")).toBeTruthy();
    expect(screen.getByText(/1 pick no longer fits and was skipped/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(onPick).toHaveBeenCalledWith(shift.slot, undefined);
    fireEvent.click(screen.getByRole("button", { name: "Reset all" }));
    expect(onResetPicks).toHaveBeenCalled();
  });

  it("shows no picker on a week already saved to ADP", () => {
    setup({ locked: true });
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText("Doe, Alex")).toBeTruthy();
  });
});
