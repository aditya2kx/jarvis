import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { dayStripOpenCount, newShifts } from "@/lib/labor/day-strip";
import type { DraftShift } from "@/lib/labor/shift-draft";

/** A day chip's open / draft / short tags: ADP open shifts only; all new draft suggestions under draft. */
export function DayStripTags({ open, drafts, shortHours = 0 }: { open: number; drafts: DraftShift[]; shortHours?: number }) {
  const openCount = dayStripOpenCount({ open });
  const drafted = newShifts(drafts);
  return (
    <>
      {openCount > 0 ? (
        <span className="mt-0.5 text-[11px] font-medium leading-none" style={{ color: LABOR_CHART_COLORS.openShift }}>
          +{openCount} open
        </span>
      ) : null}
      {drafted.length ? (
        <span className="mt-0.5 text-[11px] font-medium tabular-nums" style={{ color: LABOR_CHART_COLORS.draftShift }}>
          +{drafted.length} draft · {Number(drafted.reduce((h, d) => h + d.hours, 0).toFixed(1))}h
        </span>
      ) : null}
      {!drafted.length && shortHours > 0 ? (
        <span className="mt-0.5 text-[11px] font-medium tabular-nums text-rose-600 dark:text-rose-400">
          {shortHours.toFixed(1)}h short
        </span>
      ) : null}
    </>
  );
}
