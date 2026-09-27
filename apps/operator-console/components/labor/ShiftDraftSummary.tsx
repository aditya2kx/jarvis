"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type { Availability, DraftShift } from "@/lib/labor/shift-draft";
import { cn } from "@/lib/utils";

export const DRAFT_COLOR = LABOR_CHART_COLORS.draftShift;

const KIND_LABEL = { open: "Open", mid: "Mid", close: "Close" } as const;

function weekLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { month: "short", day: "numeric" });
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
  goalHoursWeek,
}: {
  dayLabel: string;
  shifts: DraftShift[];
  roster: Availability[];
  weekStart: string;
  existingHours: number;
  draftHours: number;
  draftCount: number;
  /** Order-driven person-hours left uncovered once the goal is spent. */
  peakLeftHours: number;
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
            <Badge variant="outline" className="h-4 px-1.5 text-[10px] font-normal">
              Mock · sample availability
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
        <Button
          size="sm"
          disabled
          title="Mock — would create ADP Open Shifts and post them to ClickUp Shift Coverage & Trades"
        >
          Finalize week
        </Button>
      </div>

      {shifts.length ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="h-8 text-xs">Shift</TableHead>
              <TableHead className="h-8 text-xs">Time</TableHead>
              <TableHead className="h-8 text-right text-xs">Hours</TableHead>
              <TableHead className="h-8 text-xs">Suggested</TableHead>
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
                  <TableCell className="py-1.5 text-xs tabular-nums">
                    {formatClockMin(s.startMin)}–{formatClockMin(s.endMin)}
                  </TableCell>
                  <TableCell className="py-1.5 text-right text-xs tabular-nums">
                    {s.hours.toFixed(1)}
                  </TableCell>
                  <TableCell className="py-1.5 text-xs">
                    {s.employee ? (
                      <span className="flex flex-wrap items-center gap-x-2">
                        <span className="font-medium">{s.employee}</span>
                        {a?.targetWeekHours != null ? (
                          <span className="text-[11px] text-muted-foreground">
                            target {a.targetWeekHours}h/week
                          </span>
                        ) : null}
                        {s.trimmed ? (
                          <span className="text-[11px] text-muted-foreground">shortened to availability</span>
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
        <p className="text-xs text-muted-foreground">
          Nothing to draft — the schedule already meets the needed headcount.
        </p>
      )}
    </div>
  );
}
