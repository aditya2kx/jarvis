/** Day-strip open / draft counts for the Labor coverage chips. */
import type { DraftShift } from "@/lib/labor/shift-draft";

/** Draft shifts that add people/hours: not fills for ADP open shifts, not saved shifts ADP already shows. */
export function newShifts(shifts: DraftShift[]): DraftShift[] {
  return shifts.filter((s) => !s.fillsOpen && !s.inAdp);
}

/** Unassigned draft suggestions (leave-open / nobody picked). */
export function draftOpenShifts(shifts: DraftShift[]): DraftShift[] {
  return newShifts(shifts).filter((s) => !s.employee);
}

/** Assigned draft suggestions (a person is on the row). */
export function draftAssignedShifts(shifts: DraftShift[]): DraftShift[] {
  return newShifts(shifts).filter((s) => !!s.employee);
}

/** Day-strip open count: ADP open lanes plus unassigned draft shifts. */
export function dayStripOpenCount(chip: { open: number }, drafts: DraftShift[] = []): number {
  return chip.open + draftOpenShifts(drafts).length;
}
