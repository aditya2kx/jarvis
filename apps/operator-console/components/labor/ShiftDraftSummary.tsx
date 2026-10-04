"use client";

import { AdpScheduleFinalize } from "@/components/labor/AdpScheduleFinalize";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { formatClockMin } from "@/lib/labor/coverage-model";
import type { HoursBreakdown } from "@/lib/labor/draft-breakdown";
import { daysLabel, type DayRule } from "@/lib/labor/schedule-inputs";
import type { PushShift } from "@/lib/labor/schedule-push";
import type { Availability, DraftShift } from "@/lib/labor/shift-draft";
import { cn } from "@/lib/utils";

export const DRAFT_COLOR = LABOR_CHART_COLORS.draftShift;
const OPEN_COLOR = LABOR_CHART_COLORS.openShift;

const KIND_LABEL = { open: "Open", mid: "Mid", close: "Close" } as const;

function weekLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const fmtH = (h: number) => `${h < 0 ? "−" : ""}${Math.abs(h).toFixed(1)}h`;

/** Where the week's hours come from, largest lever first after what's already in ADP. */
function HoursBreakdownList({
  breakdown: b,
  dayRules,
  goalHoursWeek,
}: {
  breakdown: HoursBreakdown;
  dayRules: DayRule[];
  goalHoursWeek?: number;
}) {
  const rules = dayRules
    .map((r, i) => ({ r, i, hours: b.rules.find((x) => x.id === r.id)?.hours ?? 0 }))
    .filter((x) => Math.abs(x.hours) >= 0.25);
  const lines: { key: string; label: string; hint?: string; hours: number }[] = [
    { key: "existing", label: "Already in ADP", hint: "clocked or scheduled", hours: b.existing },
    { key: "floor", label: "Minimum on the floor", hint: "Staffing basics", hours: b.floor },
    ...rules.map(({ r, i, hours }) => ({
      key: r.id,
      label: `Day rule ${i + 1} · ${daysLabel(r.days)} ${formatClockMin(r.fromMin)}–${formatClockMin(r.toMin)} · ${r.people} ${r.people === 1 ? "person" : "people"}`,
      hint: hours < 0 ? "lowers need" : undefined,
      hours,
    })),
    {
      key: "shape",
      label: "Shift length & handovers",
      hint: "shortest shift, shift times, overlap",
      hours: b.shape,
    },
    { key: "peak", label: "Busy-order shifts", hint: "only while under the goal", hours: b.peak },
  ];
  const total = lines.reduce((a, l) => a + l.hours, 0);
  const max = Math.max(...lines.map((l) => Math.abs(l.hours)), 1);
  const over = goalHoursWeek != null ? total - goalHoursWeek : 0;
  return (
    <div
      data-testid="hours-breakdown"
      className="flex max-w-2xl flex-col gap-2.5 rounded-md border bg-background/60 px-4 py-3"
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-foreground">Where the week&apos;s hours come from</span>
        <span className="text-sm tabular-nums">
          <span className={cn("font-semibold", over > 0.5 ? "text-rose-600 dark:text-rose-400" : "text-foreground")}>
            {total.toFixed(0)}h
          </span>
          {goalHoursWeek != null ? <span className="text-muted-foreground"> of {goalHoursWeek}h goal</span> : null}
        </span>
      </div>
      <ul className="flex flex-col gap-2">
        {lines.map((l) => (
          <li key={l.key} className="grid grid-cols-[minmax(0,1fr)_140px_56px] items-center gap-3 text-sm">
            <span className="min-w-0" title={l.label}>
              <span className="block truncate text-foreground">{l.label}</span>
              {l.hint ? <span className="block truncate text-xs text-muted-foreground">{l.hint}</span> : null}
            </span>
            <span className="h-2.5 overflow-hidden rounded-full bg-muted">
              <span
                className="block h-full rounded-full"
                style={{
                  width: `${(Math.abs(l.hours) / max) * 100}%`,
                  backgroundColor: l.key === "existing" ? "var(--muted-foreground)" : DRAFT_COLOR,
                  opacity: l.hours < 0 ? 0.4 : 1,
                }}
              />
            </span>
            <span
              className={cn(
                "text-right font-medium tabular-nums",
                Math.abs(l.hours) < 0.05 ? "text-muted-foreground" : "text-foreground",
              )}
            >
              {fmtH(l.hours)}
            </span>
          </li>
        ))}
      </ul>
      {over > 0.5 ? (
        <p className="border-t pt-2 text-xs text-muted-foreground">
          <span className="font-medium text-rose-600 dark:text-rose-400">{over.toFixed(0)}h over the goal.</span> Loosen the biggest lines in Scheduling rules above — the
          draft and this list update as you edit, before you save.
        </p>
      ) : null}
    </div>
  );
}

export function ShiftDraftSummary({
  dayLabel,
  shifts,
  roster,
  weekStart,
  existingHours,
  draftHours,
  draftCount,
  peakLeftHours,
  breakdown,
  dayRules,
  goalHoursWeek,
  weekShifts,
  adpWriteEnabled,
}: {
  dayLabel: string;
  shifts: DraftShift[];
  /** Every draft shift of the week — what "Save to ADP as drafts" sends. */
  weekShifts: PushShift[];
  adpWriteEnabled: boolean;
  roster: Availability[];
  weekStart: string;
  existingHours: number;
  draftHours: number;
  draftCount: number;
  /** Order-driven person-hours left uncovered once the goal is spent. */
  peakLeftHours: number;
  breakdown?: HoursBreakdown;
  dayRules?: DayRule[];
  goalHoursWeek?: number;
}) {
  const total = existingHours + draftHours;
  const over = goalHoursWeek != null && total > goalHoursWeek;
  const byName = new Map(roster.map((a) => [a.employee, a]));

  return (
    <div
      data-testid="shift-draft"
      className="flex flex-col gap-3 rounded-lg border px-3 py-3"
      style={{ borderColor: `${DRAFT_COLOR}55`, backgroundColor: `${DRAFT_COLOR}0d` }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-medium text-foreground">Draft open shifts — {dayLabel}</h4>
            <Badge variant="outline" className="h-4 px-1.5 text-[11px] font-normal">
              Works around ADP unavailability
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Week of {weekLabel(weekStart)}: {existingHours.toFixed(0)}h scheduled +{" "}
            {draftHours.toFixed(1)}h draft ({draftCount} shifts) ={" "}
            <span className={cn("font-medium tabular-nums", over ? "text-rose-600 dark:text-rose-400" : "text-foreground")}>
              {total.toFixed(0)}h
            </span>
            {goalHoursWeek != null ? ` of ${goalHoursWeek}h goal` : ""}
          </p>
          {over ? (
            <p className="text-xs text-rose-600 dark:text-rose-400">
              Over the goal: the labor floor and your day rules need these shifts, so no
              peak-order shifts were added
              {peakLeftHours > 0 ? ` (${peakLeftHours.toFixed(1)}h of peak demand uncovered)` : ""}.
            </p>
          ) : peakLeftHours > 0 ? (
            <p className="text-xs text-muted-foreground">
              Goal reached — {peakLeftHours.toFixed(1)}h of peak-order demand left uncovered. Raise
              the weekly hours goal to draft more.
            </p>
          ) : null}
        </div>
        <AdpScheduleFinalize weekStart={weekStart} shifts={weekShifts} enabled={adpWriteEnabled} />
      </div>

      {breakdown ? (
        <HoursBreakdownList breakdown={breakdown} dayRules={dayRules ?? []} goalHoursWeek={goalHoursWeek} />
      ) : null}

      {shifts.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="h-8">Shift</TableHead>
              <TableHead className="h-8">Time</TableHead>
              <TableHead className="h-8 text-right">Hours</TableHead>
              <TableHead className="h-8">Suggested</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shifts.map((s, i) => {
              const a = s.employee ? byName.get(s.employee) : undefined;
              return (
                <TableRow key={`${s.kind}-${s.startMin}-${i}`}>
                  <TableCell className="py-1.5">
                    <Badge variant="secondary" className="h-5 px-1.5 text-[11px] font-normal">
                      {KIND_LABEL[s.kind]}
                    </Badge>
                  </TableCell>
                  <TableCell className="py-1.5 tabular-nums">
                    {formatClockMin(s.startMin)}–{formatClockMin(s.endMin)}
                  </TableCell>
                  <TableCell className="py-1.5 text-right tabular-nums">
                    {s.hours.toFixed(1)}
                  </TableCell>
                  <TableCell className="py-1.5">
                    {s.employee ? (
                      <span className="flex flex-wrap items-center gap-x-2">
                        <span className="font-medium">{s.employee}</span>
                        {a?.targetWeekHours != null ? (
                          <span className="text-xs text-muted-foreground">
                            target {a.targetWeekHours}h/week
                          </span>
                        ) : null}
                        {s.trimmed ? (
                          <span className="text-xs text-muted-foreground">shortened to availability</span>
                        ) : null}
                        {s.fillsOpen ? (
                          <Badge
                            variant="outline"
                            className="h-4 px-1.5 text-[11px] font-normal"
                            style={{ borderColor: OPEN_COLOR, color: OPEN_COLOR }}
                            title="Already an open shift in ADP — assign this person there. Not included in Save to ADP."
                          >
                            fills ADP open shift · assign in ADP
                          </Badge>
                        ) : null}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">
                        Unassigned — posts as an open shift
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : (
        <p className="text-sm text-muted-foreground">
          Nothing to draft — the schedule already meets the needed headcount.
        </p>
      )}
    </div>
  );
}
