"use client";

import { ChevronLeftIcon, ChevronRightIcon, PlusIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { UnavailabilityRow } from "@/lib/bq/queries";
import { addDays, mondayOf, weekDates, weekGrid, type CellEntry } from "@/lib/labor/availability-week";
import { changeKey, type StagedChange } from "@/lib/labor/availability-changes";
import type { ScheduledShift } from "@/lib/labor/unavailability";
import { clockShort, shortDate, shortName } from "@/lib/labor/unavailability-format";
import { cn } from "@/lib/utils";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const fromRule = (r: UnavailabilityRow) => r.row_key.startsWith("rule|");

function range(fromMin: number, toMin: number): string {
  return fromMin === 0 && toMin >= 24 * 60 ? "All day" : `${clockShort(fromMin)}–${clockShort(toMin)}`;
}

function EntryChip({
  e,
  staged,
  onOpen,
}: {
  e: CellEntry<UnavailabilityRow>;
  staged: StagedChange | undefined;
  onOpen: () => void;
}) {
  const r = e.row;
  const removed = staged?.kind === "delete" || staged?.kind === "deny";
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`${r.employee} · ${range(e.fromMin, e.toMin)}${e.clash ? " · clashes with a scheduled shift" : ""}`}
      className={cn(
        "flex w-full items-center gap-1 rounded-md border px-1.5 py-1 text-left text-[11px] leading-tight font-medium tabular-nums transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:py-1.5",
        fromRule(r)
          ? "border-border text-muted-foreground italic hover:bg-muted/60"
          : r.status === "pending"
            ? "border-dashed border-foreground/40 text-foreground hover:bg-muted/60"
            : "border-transparent bg-muted text-foreground hover:bg-muted/70",
        e.clash && "border-destructive/60 text-destructive",
        staged && "ring-1 ring-primary",
        removed && "line-through opacity-60",
      )}
    >
      <span className="truncate">{range(e.fromMin, e.toMin)}</span>
      {staged ? <span className="ml-auto shrink-0 text-[10px] text-primary not-italic no-underline">{staged.kind}</span> : null}
    </button>
  );
}

export function TeamWeekGrid({
  rows,
  shifts,
  people,
  monday,
  todayIso,
  queue,
  onWeek,
  onOpenEntry,
  onAdd,
}: {
  rows: UnavailabilityRow[];
  shifts: ScheduledShift[];
  people: string[];
  monday: string;
  todayIso: string;
  queue: StagedChange[];
  onWeek: (monday: string) => void;
  onOpenEntry: (row: UnavailabilityRow, date: string) => void;
  onAdd: (employee: string, date: string) => void;
}) {
  const dates = weekDates(monday);
  const grid = weekGrid(rows, shifts, people, monday);
  const stagedByRow = new Map(queue.flatMap((c) => ("rowKey" in c ? [[c.rowKey, c] as const] : [])));
  const adds = queue.filter((c): c is Extract<StagedChange, { kind: "add" }> => c.kind === "add");
  const thisMonday = mondayOf(todayIso);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon-sm" aria-label="Previous week" onClick={() => onWeek(addDays(monday, -7))}>
            <ChevronLeftIcon />
          </Button>
          <span className="min-w-36 text-center text-sm font-medium tabular-nums">
            {shortDate(dates[0]!)} – {shortDate(dates[6]!)}
          </span>
          <Button variant="ghost" size="icon-sm" aria-label="Next week" onClick={() => onWeek(addDays(monday, 7))}>
            <ChevronRightIcon />
          </Button>
          {monday !== thisMonday ? (
            <Button variant="ghost" size="sm" onClick={() => onWeek(thisMonday)}>
              This week
            </Button>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground md:ml-auto">
          <span className="flex items-center gap-1.5">
            <span className="h-3 w-4 rounded-sm bg-muted" aria-hidden /> Approved
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-3 w-4 rounded-sm border border-dashed border-foreground/40" aria-hidden /> Pending
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded-full bg-foreground/35" aria-hidden /> Scheduled shift
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded-full bg-destructive" aria-hidden /> Clash
          </span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[760px] table-fixed border-collapse text-sm">
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="sticky left-0 z-10 w-32 bg-card px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                Person
              </th>
              {dates.map((d, i) => (
                <th
                  key={d}
                  scope="col"
                  className={cn("px-1.5 py-2 text-left text-xs font-medium tabular-nums", d === todayIso ? "text-foreground" : "text-muted-foreground")}
                >
                  {DOW[i]} {Number(d.slice(8, 10))}
                  {d === todayIso ? <span className="ml-1 rounded bg-primary/10 px-1 text-[10px] text-primary">Today</span> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.map((row) => (
              <tr key={row.employee} className="border-b border-border last:border-0">
                <th scope="row" className="sticky left-0 z-10 bg-card px-3 py-2 text-left align-top text-xs font-medium" title={row.employee}>
                  <span className="block truncate">{shortName(row.employee)}</span>
                </th>
                {row.days.map((cell) => {
                  const added = adds.filter((a) => a.employee === row.employee && a.date === cell.date);
                  const past = cell.date < todayIso;
                  return (
                    <td
                      key={cell.date}
                      className={cn("group relative h-14 border-l border-border px-1 py-1 align-top", cell.date === todayIso && "bg-primary/[0.03]", past && "bg-muted/20")}
                    >
                      <div className="flex flex-col gap-1">
                        {cell.shifts.map((s) => (
                          <span
                            key={`${s.startMin}-${s.endMin}`}
                            className={cn("flex items-center gap-1 text-[10px] tabular-nums", s.clash ? "text-destructive" : "text-muted-foreground")}
                            title={`Scheduled ${range(s.startMin, s.endMin)}`}
                          >
                            <span className={cn("h-0.5 w-2.5 shrink-0 rounded-full", s.clash ? "bg-destructive" : "bg-foreground/35")} aria-hidden />
                            {range(s.startMin, s.endMin)}
                          </span>
                        ))}
                        {cell.entries.map((e) => (
                          <EntryChip
                            key={`${e.row.row_key}-${e.row.repeat_until ?? ""}`}
                            e={e}
                            staged={stagedByRow.get(e.row.row_key)}
                            onOpen={() => onOpenEntry(e.row, cell.date)}
                          />
                        ))}
                        {added.map((a) => (
                          <span
                            key={changeKey(a)}
                            className="rounded-md border border-dashed border-primary px-1.5 py-1 text-[11px] leading-tight font-medium text-primary"
                          >
                            New · {a.allDay ? "All day" : `${a.from}–${a.to}`}
                          </span>
                        ))}
                        {!past ? (
                          <button
                            type="button"
                            onClick={() => onAdd(row.employee, cell.date)}
                            aria-label={`Add unavailability for ${row.employee} on ${cell.date}`}
                            className="flex h-6 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:opacity-40"
                          >
                            <PlusIcon className="size-3.5" />
                          </button>
                        ) : null}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
