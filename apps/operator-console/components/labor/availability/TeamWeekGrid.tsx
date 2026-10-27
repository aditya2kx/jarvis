"use client";

import { useState } from "react";
import { ChevronLeftIcon, ChevronRightIcon, PlusIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useIsPhone } from "@/components/shell/useIsPhone";
import type { UnavailabilityRow } from "@/lib/bq/queries";
import { addDays, mondayOf, weekDates, weekGrid, type CellEntry, type WeekCell } from "@/lib/labor/availability-week";
import { changeKey, type StagedChange } from "@/lib/labor/availability-changes";
import type { ScheduledShift } from "@/lib/labor/unavailability";
import { clockShort, shortDate, shortName } from "@/lib/labor/unavailability-format";
import { cn } from "@/lib/utils";

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const fromRule = (r: UnavailabilityRow) => r.row_key.startsWith("rule|");
const END = 24 * 60;

function range(fromMin: number, toMin: number): string {
  if (fromMin === 0 && toMin >= END) return "All day";
  if (fromMin === 0) return `Until ${clockShort(toMin)}`;
  if (toMin >= END) return `From ${clockShort(fromMin)}`;
  return `${clockShort(fromMin)}–${clockShort(toMin)}`;
}

type AddChange = Extract<StagedChange, { kind: "add" }>;

function EntryChip({ e, staged, onOpen }: { e: CellEntry<UnavailabilityRow>; staged: StagedChange | undefined; onOpen: () => void }) {
  const r = e.row;
  const removed = staged?.kind === "delete" || staged?.kind === "deny";
  return (
    <button
      type="button"
      onClick={onOpen}
      title={`${r.employee} · ${range(e.fromMin, e.toMin)}${fromRule(r) ? " · scheduling rule" : ""}${e.clash ? " · clashes with a scheduled shift" : ""}`}
      className={cn(
        "flex w-full items-center gap-1 rounded-md border px-1.5 py-1 text-left text-[11px] leading-tight font-medium tabular-nums transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:py-2 max-md:text-xs",
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
      {staged ? <span className="ml-auto shrink-0 text-[10px] text-primary not-italic">{staged.kind}</span> : null}
    </button>
  );
}

function DayContent({
  employee,
  cell,
  past,
  stagedByRow,
  adds,
  onOpenEntry,
  onAdd,
}: {
  employee: string;
  cell: WeekCell<UnavailabilityRow>;
  /** Past days never show a clash: the shift was worked or not, nothing is left to decide. */
  past: boolean;
  stagedByRow: Map<string, StagedChange>;
  adds: AddChange[];
  onOpenEntry: (row: UnavailabilityRow, date: string) => void;
  onAdd: (employee: string, date: string) => void;
}) {
  const added = adds.filter((a) => a.employee === employee && a.date === cell.date);
  const shown = past
    ? { ...cell, entries: cell.entries.map((e) => ({ ...e, clash: false })), shifts: cell.shifts.map((s) => ({ ...s, clash: false })) }
    : cell;
  return (
    <div className="flex flex-col gap-1">
      {shown.shifts.map((s) => (
        <span
          key={`${s.startMin}-${s.endMin}`}
          className={cn("flex items-center gap-1 text-[10px] tabular-nums max-md:text-xs", s.clash ? "text-destructive" : "text-muted-foreground")}
          title={`Scheduled ${range(s.startMin, s.endMin)}`}
        >
          <span className={cn("h-0.5 w-2.5 shrink-0 rounded-full", s.clash ? "bg-destructive" : "bg-foreground/35")} aria-hidden />
          Shift {range(s.startMin, s.endMin)}
        </span>
      ))}
      {shown.entries.map((e) => (
        <EntryChip
          key={`${e.row.row_key}-${e.row.repeat_until ?? ""}`}
          e={e}
          staged={stagedByRow.get(e.row.row_key)}
          onOpen={() => onOpenEntry(e.row, cell.date)}
        />
      ))}
      {added.map((a) => (
        <span key={changeKey(a)} className="rounded-md border border-dashed border-primary px-1.5 py-1 text-[11px] leading-tight font-medium text-primary max-md:py-2 max-md:text-xs">
          New · {a.allDay ? "All day" : `${a.from}–${a.to}`}
        </span>
      ))}
      {!past ? (
        <button
          type="button"
          onClick={() => onAdd(employee, cell.date)}
          aria-label={`Add unavailability for ${employee} on ${cell.date}`}
          className="flex h-6 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:hidden"
        >
          <PlusIcon className="size-3.5" />
        </button>
      ) : null}
    </div>
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
  const phone = useIsPhone();
  const dates = weekDates(monday);
  const [phoneDay, setPhoneDay] = useState<string | null>(null);
  const day = phoneDay && dates.includes(phoneDay) ? phoneDay : dates.includes(todayIso) ? todayIso : dates[0]!;
  const grid = weekGrid(rows, shifts, people, monday, fromRule);
  const stagedByRow = new Map(queue.flatMap((c) => ("rowKey" in c ? [[c.rowKey, c] as const] : [])));
  const adds = queue.filter((c): c is AddChange => c.kind === "add");
  const thisMonday = mondayOf(todayIso);
  const cellProps = { stagedByRow, adds, onOpenEntry, onAdd };

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
            <span className="h-3 w-4 rounded-sm border border-border" aria-hidden /> <i>Rule</i>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded-full bg-foreground/35" aria-hidden /> Shift
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded-full bg-destructive" aria-hidden /> Clash
          </span>
        </div>
      </div>

      {phone ? (
        <>
          <div className="grid grid-cols-7 gap-1" role="tablist" aria-label="Day">
            {dates.map((d, i) => {
              const busy = grid.some((r) => r.days[i]!.entries.length);
              const clash = d >= todayIso && grid.some((r) => r.days[i]!.entries.some((e) => e.clash));
              return (
                <button
                  key={d}
                  type="button"
                  role="tab"
                  aria-selected={d === day}
                  onClick={() => setPhoneDay(d)}
                  className={cn(
                    "flex h-14 flex-col items-center justify-center gap-0.5 rounded-lg border text-xs tabular-nums transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    d === day ? "border-foreground bg-muted text-foreground" : "border-border text-muted-foreground",
                  )}
                >
                  <span>{DOW[i]}</span>
                  <span className={cn("text-sm font-semibold", d === todayIso && "text-primary")}>{Number(d.slice(8, 10))}</span>
                  <span className={cn("size-1 rounded-full", clash ? "bg-destructive" : busy ? "bg-foreground/50" : "bg-transparent")} aria-hidden />
                </button>
              );
            })}
          </div>
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {grid.map((row) => {
              const cell = row.days[dates.indexOf(day)]!;
              const past = cell.date < todayIso;
              return (
                <li key={row.employee} className="flex items-start gap-3 px-3 py-2.5">
                  <span className="w-24 shrink-0 truncate pt-1.5 text-sm font-medium">{shortName(row.employee)}</span>
                  <div className="min-w-0 flex-1">
                    {cell.entries.length || cell.shifts.length ? (
                      <DayContent employee={row.employee} cell={cell} past={past} {...cellProps} />
                    ) : (
                      <span className="block pt-1.5 text-sm text-muted-foreground">Available</span>
                    )}
                  </div>
                  {!past ? (
                    <Button variant="ghost" size="icon-sm" aria-label={`Add unavailability for ${row.employee}`} onClick={() => onAdd(row.employee, cell.date)}>
                      <PlusIcon />
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </>
      ) : (
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
                  {row.days.map((cell) => (
                    <td
                      key={cell.date}
                      className={cn(
                        "group relative h-14 border-l border-border px-1 py-1 align-top",
                        cell.date === todayIso && "bg-primary/[0.03]",
                        cell.date < todayIso && "bg-muted/20",
                      )}
                    >
                      <DayContent employee={row.employee} cell={cell} past={cell.date < todayIso} {...cellProps} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
