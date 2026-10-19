import { isOpenLane, type CoveragePersonDay } from "@/lib/labor/coverage-model";
import { inAdpSchedule, type PushShift, type PushStatus } from "@/lib/labor/schedule-push";
import { paidHours, type DraftShift, type MealRule } from "@/lib/labor/shift-draft";

/** One saved (locked-week) shift as a draft row, matched against ADP's synced schedule for its day. */
export function planShift(
  s: PushShift & { status: PushStatus },
  lanes: readonly CoveragePersonDay[],
  meal: MealRule,
  sameDay: readonly PushShift[],
): DraftShift {
  const kind = s.startMin <= 450 ? "open" : s.endMin >= 1200 ? "close" : "mid";
  const adp = lanes.map((l) => ({
    employee: l.employee,
    open: isOpenLane(l),
    segments: l.segments.filter((g) => g.kind !== "actual"),
  }));
  const asPerson = inAdpSchedule(s, adp);
  // ADP unassigns a saved shift when the person's unavailability is approved; it's still in ADP, as an open shift.
  const adpOpen =
    !asPerson && s.employee != null && (s.status === "drafted" || s.status === "published") &&
    inAdpSchedule({ ...s, employee: null }, adp);
  // A saved open shift someone took in ADP (Write to ADP, or claimed there) is that person's shift now.
  const takenBy =
    !asPerson && s.employee == null && s.status === "published"
      ? adp.find(
          (l) =>
            !l.open &&
            !sameDay.some((p) => p.employee === l.employee && p.startMin === s.startMin && p.endMin === s.endMin) &&
            l.segments.some((g) => g.startMin === s.startMin && g.endMin === s.endMin),
        )?.employee
      : undefined;
  const { status: _status, ...shift } = s;
  return {
    ...shift,
    kind,
    hours: paidHours(s.startMin, s.endMin, meal),
    trimmed: false,
    inAdp: asPerson || adpOpen || takenBy != null,
    adpOpen,
    takenBy,
  };
}
