import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { dayStripOpenCount, draftAssignedShifts, newShifts } from "@/lib/labor/day-strip";
import type { DraftShift } from "@/lib/labor/shift-draft";

/** A day chip's open / draft / short tags: ADP open shifts plus the draft's unassigned ones read as open. */
export function DayStripTags({ open, drafts, shortHours = 0 }: { open: number; drafts: DraftShift[]; shortHours?: number }) {
  const openCount = dayStripOpenCount({ open }, drafts);
  const assigned = draftAssignedShifts(drafts);
  return (
    <>
      {openCount > 0 ? (
        <span className="mt-0.5 text-[11px] font-medium leading-none" style={{ color: LABOR_CHART_COLORS.openShift }}>
          +{openCount} open
        </span>
      ) : null}
      {assigned.length ? (
        <span className="mt-0.5 text-[11px] font-medium tabular-nums" style={{ color: LABOR_CHART_COLORS.draftShift }}>
          +{assigned.length} draft · {Number(assigned.reduce((h, d) => h + d.hours, 0).toFixed(1))}h
        </span>
      ) : null}
      {!newShifts(drafts).length && shortHours > 0 ? (
        <span className="mt-0.5 text-[11px] font-medium tabular-nums text-rose-600 dark:text-rose-400">
          {shortHours.toFixed(1)}h short
        </span>
      ) : null}
    </>
  );
}
