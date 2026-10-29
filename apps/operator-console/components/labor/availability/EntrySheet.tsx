"use client";

import { useState, type ReactNode } from "react";
import { ChevronRightIcon } from "lucide-react";
import { StagedBadge } from "@/components/labor/StagedBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import type { UnavailabilityRow } from "@/lib/bq/queries";
import { bulkAdds, entryError, onOrAfter, type ChangeScope, type EntryFields, type StagedChange } from "@/lib/labor/availability-changes";
import {
  personShifts,
  shiftError,
  shiftKey,
  SHIFT_LABEL,
  timeText,
  toHhmm,
  toMin,
  type PersonShift,
  type ShiftChange,
  type Times,
} from "@/lib/labor/adp-changes";
import { approvedByPerson, collapseWeekly, coversDate, scheduleConflicts, type ScheduledShift } from "@/lib/labor/unavailability";
import { addDays, clock, dayLabel, hoursText, lineText, shortDate, WEEKDAYS, whenText } from "@/lib/labor/unavailability-format";
import { cn } from "@/lib/utils";

export type SheetTarget =
  | { mode: "add"; employee: string | null; date: string }
  | { mode: "entry"; row: UnavailabilityRow; date: string }
  /** One person's whole picture: shifts, requests, standing entries, clashes. */
  | { mode: "person"; employee: string }
  | { mode: "shift-add"; employee: string | null; date: string }
  /** One of a person's ADP shifts (draft or published): edit times, reassign or delete. */
  | { mode: "shift"; employee: string; shift: PersonShift }
  | { mode: "open-add"; date: string }
  /** One ADP open shift: assign (to `options`, the people the rules allow), edit times or delete. */
  | { mode: "open-shift"; slot: string; date: string; startMin: number; endMin: number; options: string[]; suggested: string | null };

type UnavailTarget = Extract<SheetTarget, { mode: "add" | "entry" }>;
type ShiftTarget = Extract<SheetTarget, { mode: "shift-add" | "shift" | "open-add" | "open-shift" }>;

const fromRule = (r: UnavailabilityRow) => r.row_key.startsWith("rule|");

function fieldsFor(target: UnavailTarget, todayIso: string): EntryFields {
  if (target.mode !== "entry") {
    const date = target.mode === "add" && target.date > todayIso ? target.date : todayIso;
    return { date, allDay: true, from: "09:00", to: "17:00", weekly: false, until: null };
  }
  const r = target.row;
  return {
    date: r.first_date,
    allDay: r.all_day || !r.from_time || !r.to_time,
    from: r.from_time?.slice(0, 5) ?? "09:00",
    to: r.to_time?.slice(0, 5) ?? "17:00",
    weekly: r.repeat_weekday != null,
    until: r.repeat_until,
  };
}

/** The entry's next date on or after `from` (its first date if none is left). */
function nextDate(r: UnavailabilityRow, from: string): string {
  return nextDates(r, from)[0] ?? r.first_date;
}

/** Up to four dates the entry still covers, starting from `from`. */
function nextDates(r: UnavailabilityRow, from: string): string[] {
  const out: string[] = [];
  const start = from < r.first_date ? r.first_date : from;
  for (let i = 0; i < 28 && out.length < 4; i++) {
    const d = addDays(start, i);
    if (coversDate(r, d)) out.push(d);
  }
  return out;
}

function entrySummary(employee: string, f: EntryFields): string {
  const hours = hoursText({ all_day: f.allDay, from_time: f.from, to_time: f.to });
  if (!f.weekly) return `${employee} · ${dayLabel(f.date)} · ${hours}`;
  const wd = WEEKDAYS[(new Date(`${f.date}T12:00:00Z`).getUTCDay() + 6) % 7];
  return `${employee} · Every ${wd} · ${hours} · ${shortDate(f.date)}${f.until ? ` – ${shortDate(f.until)}` : " onward"}`;
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="flex w-fit items-center gap-1 rounded-lg border border-border p-0.5" role="radiogroup" aria-label={label}>
      {options.map(([v, text]) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className={cn(
            "h-7 rounded-md px-2.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:h-9",
            value === v ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

function TimeFields({ fields, onChange }: { fields: EntryFields; onChange: (f: EntryFields) => void }) {
  const set = (patch: Partial<EntryFields>) => onChange({ ...fields, ...patch });
  return (
    <>
      <label className="flex items-center justify-between gap-3 text-sm">
        <span>All day</span>
        <Switch checked={fields.allDay} onCheckedChange={(v) => set({ allDay: v })} />
      </label>
      {!fields.allDay ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label htmlFor="unavail-from">From</Label>
            <Input id="unavail-from" className={PICKER_INPUT} type="time" step={900} value={fields.from ?? ""} onChange={(e) => set({ from: e.target.value })} />
          </div>
          <div className="flex min-w-0 flex-col gap-1.5">
            <Label htmlFor="unavail-to">To</Label>
            <Input id="unavail-to" className={PICKER_INPUT} type="time" step={900} value={fields.to ?? ""} onChange={(e) => set({ to: e.target.value })} />
          </div>
        </div>
      ) : null}
    </>
  );
}

/** iOS Safari gives date/time inputs an intrinsic width wider than a phone drawer column. */
const PICKER_INPUT = "appearance-none [&::-webkit-date-and-time-value]:text-left";

const weekdayOf = (iso: string) => (new Date(`${iso}T12:00:00Z`).getUTCDay() + 6) % 7;

type DayMark = "off" | "requested" | "shift";

/** Tap-to-pick days, Mon–Sun weeks from the start date's week; marks show what the person already has. */
function DayPicker({
  start,
  todayIso,
  days,
  marks,
  onChange,
}: {
  start: string;
  todayIso: string;
  days: string[];
  marks: (iso: string) => DayMark | null;
  onChange: (days: string[]) => void;
}) {
  const [weeks, setWeeks] = useState(6);
  const monday = addDays(start, -weekdayOf(start));
  const picked = new Set(days);
  const cells = Array.from({ length: weeks * 7 }, (_, i) => addDays(monday, i));
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-7 gap-1 text-center" role="group" aria-label="Days">
        {WEEKDAYS.map((d) => (
          <span key={d} className="text-[11px] font-medium text-muted-foreground">
            {d}
          </span>
        ))}
        {cells.map((iso) => {
          const on = picked.has(iso);
          const past = iso < todayIso;
          const mark = marks(iso);
          const dayNum = Number(iso.slice(8, 10));
          return (
            <button
              key={iso}
              type="button"
              disabled={past}
              aria-pressed={on}
              aria-label={`${dayLabel(iso)}${mark === "shift" ? " · scheduled" : mark ? ` · already ${mark}` : ""}`}
              onClick={() => onChange(on ? days.filter((d) => d !== iso) : [...days, iso])}
              className={cn(
                "relative flex h-10 flex-col items-center justify-center rounded-md border text-sm tabular-nums transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-35",
                on
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-transparent hover:border-border hover:bg-muted/60",
                !on && (mark === "off" || mark === "requested") && "bg-muted/50 text-muted-foreground line-through",
                iso === todayIso && !on && "ring-1 ring-inset ring-primary/40",
              )}
            >
              <span className="leading-none">{dayNum === 1 || iso === cells[0] ? shortDate(iso) : dayNum}</span>
              {mark === "shift" ? (
                <span aria-hidden className={cn("mt-1 size-1 rounded-full", on ? "bg-primary-foreground" : "bg-foreground/60")} />
              ) : null}
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <span aria-hidden className="size-1 rounded-full bg-foreground/60" /> Scheduled
          </span>
          <span className="line-through">12</span>
          <span className="-ml-2">Already off</span>
        </span>
        <span className="flex items-center gap-1">
          {days.length ? (
            <Button size="xs" variant="ghost" onClick={() => onChange([])}>
              Clear
            </Button>
          ) : null}
          <Button size="xs" variant="ghost" onClick={() => setWeeks((w) => w + 4)}>
            More weeks
          </Button>
        </span>
      </div>
    </div>
  );
}

/** Drawer for one entry (or a new one). Every button stages a change; nothing reaches ADP until Apply. */
export function EntrySheet({
  target,
  roster,
  todayIso,
  offRows = [],
  shifts = [],
  canAvailability = true,
  stagedShift = () => undefined,
  onTarget,
  onClose,
  onStage,
  onStageShift = () => {},
}: {
  target: SheetTarget | null;
  roster: string[];
  todayIso: string;
  /** Existing unavailability + scheduled shifts, marked on the add drawer's day picker. */
  offRows?: UnavailabilityRow[];
  shifts?: ScheduledShift[];
  /** Labor › Availability access: unavailability can be changed, not only seen. */
  canAvailability?: boolean;
  /** The staged change for a shift, by `shiftKey`. */
  stagedShift?: (key: string) => ShiftChange | undefined;
  /** Move the drawer to another target (person → entry / add). */
  onTarget: (t: SheetTarget) => void;
  onClose: () => void;
  onStage: (c: StagedChange) => void;
  onStageShift?: (c: ShiftChange) => void;
}) {
  const key = !target ? "" : targetKey(target);
  return (
    <Sheet open={target != null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        {target ? (
          target.mode === "person" ? (
            <PersonBody
              key={key}
              employee={target.employee}
              todayIso={todayIso}
              offRows={offRows}
              shifts={shifts}
              canAvailability={canAvailability}
              stagedShift={stagedShift}
              onTarget={onTarget}
            />
          ) : target.mode === "add" || target.mode === "entry" ? (
            <SheetBody key={key} target={target} roster={roster} todayIso={todayIso} offRows={offRows} shifts={shifts} onClose={onClose} onStage={onStage} />
          ) : (
            <ShiftBody key={key} target={target} roster={roster} todayIso={todayIso} offRows={offRows} shifts={shifts} onClose={onClose} onStage={onStageShift} />
          )
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function targetKey(t: SheetTarget): string {
  switch (t.mode) {
    case "add":
      return `add-${t.employee}-${t.date}`;
    case "entry":
      return `${t.row.row_key}-${t.date}`;
    case "person":
      return `person-${t.employee}`;
    case "shift-add":
      return `shift-add-${t.employee}-${t.date}`;
    case "shift":
      return `shift-${t.employee}-${t.shift.date}-${t.shift.startMin}`;
    case "open-add":
      return `open-add-${t.date}`;
    case "open-shift":
      return `open-shift-${t.slot}`;
    default: {
      const never: never = t;
      return never;
    }
  }
}

const SHIFT_DAYS = 14;

/** "Will delete" / "Will change to …" / "Will go to …" for a shift with a staged change. */
function stagedShiftText(c: ShiftChange): string {
  if (c.kind === "shift-edit") return `Will change to ${timeText(c.to)}`;
  if (c.kind === "shift-reassign") return `Will go to ${c.toEmployee}`;
  return c.kind === "shift-delete" ? "Will delete" : SHIFT_LABEL[c.kind];
}

/**
 * A row that opens something. It looks like a list cell with a chevron so it reads as tappable on
 * a phone, where there is no hover; `expanded` turns the chevron down for a row that unfolds.
 */
function OpensRow({
  expanded,
  disabled,
  className,
  children,
  onClick,
}: {
  expanded?: boolean;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex min-h-11 w-full items-center gap-2 rounded-lg border border-border px-3 py-2 text-left text-sm tabular-nums transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default disabled:border-transparent disabled:px-2 disabled:hover:bg-transparent",
        className,
      )}
    >
      <span className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-2 gap-y-1">{children}</span>
      {disabled ? null : (
        <ChevronRightIcon
          aria-hidden
          data-affordance="opens"
          className={cn("size-4 shrink-0 text-muted-foreground transition-transform", expanded && "rotate-90")}
        />
      )}
    </button>
  );
}

function PersonBody({
  employee,
  todayIso,
  offRows,
  shifts,
  canAvailability,
  stagedShift,
  onTarget,
}: {
  employee: string;
  todayIso: string;
  offRows: UnavailabilityRow[];
  shifts: ScheduledShift[];
  canAvailability: boolean;
  stagedShift: (key: string) => ShiftChange | undefined;
  onTarget: (t: SheetTarget) => void;
}) {
  const tomorrow = addDays(todayIso, 1);
  const upcoming = personShifts(shifts, employee, tomorrow);
  const [allShifts, setAllShifts] = useState(false);
  const horizon = addDays(tomorrow, SHIFT_DAYS);
  const shown = allShifts ? upcoming : upcoming.filter((x) => x.date < horizon);
  const mine = offRows.filter((r) => r.employee === employee);
  const pending = collapseWeekly(mine.filter((r) => r.status === "pending" && !fromRule(r)));
  const lines = approvedByPerson(mine, todayIso)[0]?.lines ?? [];
  const clashes = scheduleConflicts(mine, shifts.filter((x) => x.employee === employee && x.date >= todayIso));
  const readThrough = offRows.reduce((m, r) => (r.status === "approved" && !fromRule(r) && r.first_date > m ? r.first_date : m), "");
  const lastWeekFrom = readThrough ? addDays(readThrough, -6) : "";
  const nextWeek = addDays(todayIso, 7);
  const [open, setOpen] = useState<string | null>(null);
  const entry = canAvailability ? (r: UnavailabilityRow) => onTarget({ mode: "entry", row: r, date: nextDate(r, todayIso) }) : undefined;
  return (
    <>
      <SheetHeader>
        <SheetTitle className="truncate pr-8">{employee}</SheetTitle>
        <SheetDescription>Shifts and unavailability from ADP, plus your scheduling rules.</SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-5 px-4">
        <section className="flex flex-col gap-1.5" data-testid="person-shifts">
          <h4 className="text-xs font-medium text-muted-foreground">Upcoming shifts</h4>
          {shown.length ? (
            shown.map((x) => {
              const change = stagedShift(shiftKey({ kind: "shift-delete", employee, ...x }));
              return (
                <OpensRow
                  key={`${x.date}-${x.startMin}`}
                  className={cn(change && "bg-primary/5")}
                  onClick={() => onTarget({ mode: "shift", employee, shift: x })}
                >
                  <span className={cn(change?.kind === "shift-delete" && "line-through opacity-60")}>
                    {dayLabel(x.date)} · {timeText(x)}
                  </span>
                  {change ? (
                    <StagedBadge remove={change.kind === "shift-delete"}>{stagedShiftText(change)}</StagedBadge>
                  ) : x.draft ? (
                    <Badge variant="outline">Draft</Badge>
                  ) : null}
                </OpensRow>
              );
            })
          ) : (
            <p className="px-2 text-sm text-muted-foreground">No shifts in ADP from tomorrow on.</p>
          )}
          {upcoming.length > shown.length ? (
            <Button size="xs" variant="ghost" className="w-fit" onClick={() => setAllShifts(true)}>
              Show all {upcoming.length}
            </Button>
          ) : null}
        </section>
        {pending.length ? (
          <section className="flex flex-col gap-1.5">
            <h4 className="text-xs font-medium text-muted-foreground">Requests waiting on you</h4>
            {pending.map((r) => (
              <OpensRow key={`${r.row_key}-${r.repeat_until ?? ""}`} disabled={!entry} onClick={() => entry?.(r)}>
                <span className="text-amber-600 dark:text-amber-400">{whenText(r)}</span>
              </OpensRow>
            ))}
          </section>
        ) : null}
        <section className="flex flex-col gap-1.5">
          <h4 className="text-xs font-medium text-muted-foreground">Standing unavailability</h4>
          {lines.length ? (
            lines.map((l) => {
              const key = `${l.weekdays.join("")}-${l.date}-${l.from_time}`;
              const many = l.rows.length > 1;
              return (
                <div key={key} className="flex flex-col gap-1.5">
                  <OpensRow
                    expanded={many ? open === key : undefined}
                    disabled={!many && !entry}
                    onClick={() => (many ? setOpen(open === key ? null : key) : entry?.(l.rows[0]!))}
                  >
                    <span className={cn(l.rows.every(fromRule) && "italic")}>
                      {lineText(l, lastWeekFrom, nextWeek)}
                      {l.rows.every(fromRule) ? " · scheduling rule" : ""}
                    </span>
                    {many ? <span className="text-xs text-muted-foreground">{l.rows.length} ADP entries</span> : null}
                  </OpensRow>
                  {many && open === key
                    ? l.rows.map((r) => (
                        <OpensRow key={r.row_key} className="ml-4 w-auto text-muted-foreground" disabled={!entry} onClick={() => entry?.(r)}>
                          {whenText(r)}
                        </OpensRow>
                      ))
                    : null}
                </div>
              );
            })
          ) : (
            <p className="px-2 text-sm text-muted-foreground">Nothing marked — available any time.</p>
          )}
        </section>
        {clashes.length ? (
          <section className="flex flex-col gap-1">
            <h4 className="text-xs font-medium text-destructive">Scheduled while unavailable</h4>
            {clashes.map((c) => (
              <p key={`${c.date}-${c.shift.startMin}`} className="px-2 text-sm tabular-nums">
                {dayLabel(c.date)} · scheduled {clock(c.shift.startMin)}–{clock(c.shift.endMin)}
              </p>
            ))}
          </section>
        ) : null}
      </div>
      <SheetFooter className="flex-row justify-end">
        {canAvailability ? (
          <Button variant="outline" onClick={() => onTarget({ mode: "add", employee, date: todayIso })}>
            Add unavailability
          </Button>
        ) : null}
        <Button onClick={() => onTarget({ mode: "shift-add", employee, date: tomorrow })}>Add shift</Button>
      </SheetFooter>
    </>
  );
}

/** Which days: pick dates in a grid, or weekly on chosen weekdays — shared by add and edit. */
function WhenEditor({
  repeat,
  onRepeat,
  days,
  onDays,
  weekdays,
  onWeekdays,
  fields,
  onFields,
  todayIso,
  marks,
}: {
  repeat: "days" | "weekly";
  onRepeat: (r: "days" | "weekly") => void;
  days: string[];
  onDays: (d: string[]) => void;
  weekdays: number[];
  onWeekdays: (w: number[]) => void;
  fields: EntryFields;
  onFields: (f: EntryFields) => void;
  todayIso: string;
  marks: (iso: string) => DayMark | null;
}) {
  return (
    <>
      <Segmented
        label="Which days"
        value={repeat}
        onChange={onRepeat}
        options={[
          ["days", "Pick days"],
          ["weekly", "Repeats weekly"],
        ]}
      />
      {repeat === "days" ? (
        <DayPicker start={[...days].sort()[0] ?? fields.date} todayIso={todayIso} days={days} marks={marks} onChange={onDays} />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label>On</Label>
            <div className="grid grid-cols-7 gap-1" role="group" aria-label="Weekdays">
              {WEEKDAYS.map((d, i) => {
                const on = weekdays.includes(i);
                return (
                  <button
                    key={d}
                    type="button"
                    aria-pressed={on}
                    onClick={() => onWeekdays(on ? weekdays.filter((x) => x !== i) : [...weekdays, i])}
                    className={cn(
                      "h-9 rounded-md border text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      on ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                  >
                    {d}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor="unavail-start">Starts</Label>
              <Input id="unavail-start" className={PICKER_INPUT} type="date" value={fields.date} onChange={(e) => onFields({ ...fields, date: e.target.value })} />
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor="unavail-until">Until (optional)</Label>
              <Input
                id="unavail-until"
                className={PICKER_INPUT}
                type="date"
                min={fields.date}
                value={fields.until ?? ""}
                onChange={(e) => onFields({ ...fields, until: e.target.value || null })}
              />
            </div>
          </div>
          {weekdays.length ? (
            <p className="text-xs text-muted-foreground">
              First dates: {weekdays.map((w) => onOrAfter(fields.date, w)).sort().map(shortDate).join(" · ")}
            </p>
          ) : null}
        </div>
      )}
      <TimeFields fields={fields} onChange={onFields} />
    </>
  );
}

function SheetBody({
  target,
  roster,
  todayIso,
  offRows,
  shifts,
  onClose,
  onStage,
}: {
  target: UnavailTarget;
  roster: string[];
  todayIso: string;
  offRows: UnavailabilityRow[];
  shifts: ScheduledShift[];
  onClose: () => void;
  onStage: (c: StagedChange) => void;
}) {
  const row = target.mode === "entry" ? target.row : null;
  const [fields, setFields] = useState<EntryFields>(() => fieldsFor(target, todayIso));
  const [employee, setEmployee] = useState<string | null>(row ? row.employee : target.mode === "add" ? target.employee : null);
  const [scope, setScope] = useState<ChangeScope>("series");
  const [repeat, setRepeat] = useState<"days" | "weekly">(row?.repeat_weekday != null ? "weekly" : "days");
  const [days, setDays] = useState<string[]>(() => (row && row.repeat_weekday == null ? [row.first_date] : []));
  const [weekdays, setWeekdays] = useState<number[]>(() => (row?.repeat_weekday != null ? [row.repeat_weekday] : []));
  const [initial] = useState(() => ({ fields: JSON.stringify(fields), when: JSON.stringify({ days, weekdays, repeat }) }));
  const stageAndClose = (c: StagedChange) => {
    onStage(c);
    onClose();
  };
  const marks = (iso: string): DayMark | null => {
    if (!employee) return null;
    const off = offRows.filter((r) => r.employee === employee && r.row_key !== row?.row_key && coversDate(r, iso));
    if (off.some((r) => r.status === "approved")) return "off";
    if (off.length) return "requested";
    return shifts.some((x) => x.employee === employee && x.date === iso) ? "shift" : null;
  };
  const count = repeat === "days" ? days.length : weekdays.length;
  const pickError = !count ? (repeat === "days" ? "Pick at least one day." : "Pick at least one weekday.") : null;
  const changes = (who: string) =>
    bulkAdds(who, fields, repeat === "days" ? { days } : { weekdays }, (f) => entrySummary(who, f));
  const editor = (
    <WhenEditor
      repeat={repeat}
      onRepeat={setRepeat}
      days={days}
      onDays={setDays}
      weekdays={weekdays}
      onWeekdays={setWeekdays}
      fields={fields}
      onFields={setFields}
      todayIso={todayIso}
      marks={marks}
    />
  );

  if (target.mode === "add") {
    const err = !employee ? "Pick a person." : (pickError ?? entryError(fields));
    const noun = repeat === "days" ? (count === 1 ? "day" : "days") : count === 1 ? "weekly repeat" : "weekly repeats";
    return (
      <>
        <SheetHeader>
          <SheetTitle>Add unavailability</SheetTitle>
          <SheetDescription>Pick every day at once. They wait with your other changes and go to ADP together when you apply.</SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4">
          <div className="flex flex-col gap-1.5">
            <Label>Person</Label>
            <Select value={employee} onValueChange={(v) => v && setEmployee(String(v))}>
              <SelectTrigger className="w-full" aria-label="Person">
                <SelectValue placeholder="Pick a person" />
              </SelectTrigger>
              <SelectContent>
                {roster.map((n) => (
                  <SelectItem key={n} value={n}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {editor}
        </div>
        <SheetFooter className="flex-row justify-end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={err != null}
            title={err ?? undefined}
            onClick={() => {
              for (const c of changes(employee!)) onStage(c);
              onClose();
            }}
          >
            {count > 1 ? `Add ${count} ${noun} to changes` : "Add to changes"}
          </Button>
        </SheetFooter>
      </>
    );
  }

  const r = target.row;
  const weekly = r.repeat_weekday != null;
  const onlyDate = weekly && scope === "date";
  const header = (
    <SheetHeader>
      <div className="flex items-center gap-2 pr-8">
        <SheetTitle className="truncate">{r.employee}</SheetTitle>
        {fromRule(r) ? (
          <Badge variant="outline">Scheduling rule</Badge>
        ) : r.status === "pending" ? (
          <Badge variant="outline">Pending</Badge>
        ) : (
          <Badge variant="secondary">Approved</Badge>
        )}
      </div>
      <SheetDescription>{whenText(r)}</SheetDescription>
    </SheetHeader>
  );

  if (fromRule(r)) {
    return (
      <>
        {header}
        <p className="px-4 text-sm text-muted-foreground">
          Entered as an &ldquo;Unavailable on dates&rdquo; scheduling rule, not in ADP. Change it under Scheduling rules at the top
          of this panel.
        </p>
      </>
    );
  }

  if (r.status === "pending") {
    const summary = `${r.employee} · ${whenText(r)}`;
    return (
      <>
        {header}
        <p className="px-4 text-sm text-muted-foreground">
          Approving adds it to {r.employee}&apos;s schedule in ADP and ADP notifies them. Denying removes the request in ADP.
        </p>
        <SheetFooter className="flex-row justify-end">
          <Button variant="outline" onClick={() => stageAndClose({ kind: "deny", rowKey: r.row_key, employee: r.employee, summary })}>
            Deny
          </Button>
          <Button onClick={() => stageAndClose({ kind: "approve", rowKey: r.row_key, employee: r.employee, summary })}>Approve</Button>
        </SheetFooter>
      </>
    );
  }

  // The entry itself becomes the first day / weekday picked; any extra picks are new entries.
  const save = () => {
    if (onlyDate) {
      const f = { ...fields, date: target.date, weekly: false, until: null };
      stageAndClose({ kind: "edit", rowKey: r.row_key, employee: r.employee, scope: "date", summary: entrySummary(r.employee, f), ...f });
      return;
    }
    const [first, ...rest] = changes(r.employee);
    if (first?.kind !== "add") return;
    onStage({ ...first, kind: "edit", rowKey: r.row_key, scope: "series" });
    for (const c of rest) onStage(c);
    onClose();
  };
  const unchanged =
    JSON.stringify(fields) === initial.fields && (onlyDate || JSON.stringify({ days, weekdays, repeat }) === initial.when);
  const err = unchanged ? "Change the days or hours." : onlyDate ? entryError(fields) : (pickError ?? entryError(fields));
  return (
    <>
      {header}
      <div className="flex flex-col gap-4 px-4">
        {weekly ? (
          <div className="flex flex-col gap-1 text-sm">
            <span className="text-xs font-medium text-muted-foreground">Next dates</span>
            <span className="tabular-nums">{nextDates(r, target.date).map(shortDate).join(" · ") || "None left"}</span>
          </div>
        ) : null}
        {weekly ? (
          <div className="flex flex-col gap-1.5">
            <Label>Apply to</Label>
            <Segmented
              label="Apply to"
              value={scope}
              onChange={setScope}
              options={[
                ["series", "Whole series"],
                ["date", `Only ${dayLabel(target.date)}`],
              ]}
            />
          </div>
        ) : null}
        {onlyDate ? <TimeFields fields={fields} onChange={setFields} /> : editor}
        <p className="text-xs text-muted-foreground">Nothing changes in ADP until you press Apply to ADP.</p>
      </div>
      <SheetFooter className="flex-row items-center">
        <Button
          variant="destructive"
          className="mr-auto"
          onClick={() =>
            stageAndClose({
              kind: "delete",
              rowKey: r.row_key,
              employee: r.employee,
              scope: weekly ? scope : "series",
              date: onlyDate ? target.date : r.first_date,
              summary: `${r.employee} · ${onlyDate ? `${dayLabel(target.date)} only` : whenText(r)}`,
            })
          }
        >
          Delete
        </Button>
        <Button disabled={err != null} title={err ?? undefined} onClick={save}>
          {!onlyDate && count > 1 ? `Save + add ${count - 1} more` : "Save to changes"}
        </Button>
      </SheetFooter>
    </>
  );
}

function ShiftTimes({ value, onChange }: { value: Times; onChange: (t: Times) => void }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="flex min-w-0 flex-col gap-1.5">
        <Label htmlFor="shift-start">Starts</Label>
        <Input
          id="shift-start"
          className={PICKER_INPUT}
          type="time"
          step={900}
          value={Number.isFinite(value.startMin) ? toHhmm(value.startMin) : ""}
          onChange={(e) => onChange({ ...value, startMin: toMin(e.target.value) })}
        />
      </div>
      <div className="flex min-w-0 flex-col gap-1.5">
        <Label htmlFor="shift-end">Ends</Label>
        <Input
          id="shift-end"
          className={PICKER_INPUT}
          type="time"
          step={900}
          value={Number.isFinite(value.endMin) ? toHhmm(value.endMin) : ""}
          onChange={(e) => onChange({ ...value, endMin: toMin(e.target.value) })}
        />
      </div>
    </div>
  );
}

function PersonSelect({
  value,
  roster,
  label = "Person",
  onChange,
}: {
  value: string | null;
  roster: string[];
  label?: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <Select value={value} onValueChange={(v) => v && onChange(String(v))}>
        <SelectTrigger className="w-full" aria-label={label}>
          <SelectValue placeholder="Pick a person" />
        </SelectTrigger>
        <SelectContent>
          {roster.map((n) => (
            <SelectItem key={n} value={n}>
              {n}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

const DEFAULT_TIMES: Times = { startMin: 9 * 60, endMin: 15 * 60 };

/** Add / change / delete ADP shifts and open shifts; every button stages into the Changes to ADP tray. */
function ShiftBody({
  target,
  roster,
  todayIso,
  offRows,
  shifts,
  onClose,
  onStage,
}: {
  target: ShiftTarget;
  roster: string[];
  todayIso: string;
  offRows: UnavailabilityRow[];
  shifts: ScheduledShift[];
  onClose: () => void;
  onStage: (c: ShiftChange) => void;
}) {
  const existing = target.mode === "shift" ? target.shift : target.mode === "open-shift" ? target : null;
  const [times, setTimes] = useState<Times>(existing ? { startMin: existing.startMin, endMin: existing.endMin } : DEFAULT_TIMES);
  const [employee, setEmployee] = useState<string | null>(
    target.mode === "shift-add" || target.mode === "shift" ? target.employee : target.mode === "open-shift" ? target.suggested : null,
  );
  const [days, setDays] = useState<string[]>(() => (target.mode === "shift-add" || target.mode === "open-add" ? [target.date > todayIso ? target.date : addDays(todayIso, 1)] : []));
  const [count, setCount] = useState(1);
  const tomorrow = addDays(todayIso, 1);
  const stageAll = (changes: ShiftChange[]) => {
    for (const c of changes) onStage(c);
    onClose();
  };
  const firstError = (changes: ShiftChange[], empty: string) =>
    changes.length ? (changes.map((c) => shiftError(c, todayIso)).find((e) => e != null) ?? null) : empty;
  const marks = (who: string | null) => (iso: string): DayMark | null => {
    if (!who) return null;
    const off = offRows.filter((r) => r.employee === who && coversDate(r, iso));
    if (off.some((r) => r.status === "approved")) return "off";
    if (off.length) return "requested";
    return shifts.some((x) => x.employee === who && x.date === iso) ? "shift" : null;
  };
  const footer = (err: string | null, label: string, changes: ShiftChange[]) => (
    <SheetFooter className="flex-row justify-end">
      <Button variant="ghost" onClick={onClose}>
        Cancel
      </Button>
      <Button disabled={err != null} title={err ?? undefined} onClick={() => stageAll(changes)}>
        {label}
      </Button>
    </SheetFooter>
  );

  if (target.mode === "shift-add" || target.mode === "open-add") {
    const open = target.mode === "open-add";
    const changes: ShiftChange[] = [...days].sort().map((date) =>
      open ? { kind: "open-add", date, count, ...times } : { kind: "shift-add", date, employee: employee ?? "", ...times },
    );
    const err = !open && !employee ? "Pick a person." : firstError(changes, "Pick at least one day.");
    const n = changes.length;
    return (
      <>
        <SheetHeader>
          <SheetTitle>{open ? "Add open shift" : "Add shift"}</SheetTitle>
          <SheetDescription>
            {open
              ? "Anyone eligible can pick an open shift up in ADP. Pick every day at once; they go to ADP with your other changes."
              : "Pick every day at once. The shifts are published in ADP when you apply, so the person sees them."}
          </SheetDescription>
        </SheetHeader>
        <div className="flex flex-col gap-4 px-4">
          {open ? null : <PersonSelect value={employee} roster={roster} onChange={setEmployee} />}
          <DayPicker start={[...days].sort()[0] ?? tomorrow} todayIso={tomorrow} days={days} marks={marks(open ? null : employee)} onChange={setDays} />
          <ShiftTimes value={times} onChange={setTimes} />
          {open ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="open-count">How many each day</Label>
              <Input
                id="open-count"
                type="number"
                inputMode="numeric"
                min={1}
                max={10}
                className="w-24"
                value={count}
                onChange={(e) => setCount(Math.round(Number(e.target.value)))}
              />
            </div>
          ) : null}
        </div>
        {footer(err, n > 1 ? `Add ${n} days to changes` : "Add to changes", changes)}
      </>
    );
  }

  const isOpen = target.mode === "open-shift";
  const shift = isOpen ? target : target.shift;
  const base = { date: shift.date, startMin: shift.startMin, endMin: shift.endMin };
  const past = shift.date <= todayIso;
  const header = (
    <SheetHeader>
      <div className="flex items-center gap-2 pr-8">
        <SheetTitle className={cn("truncate", isOpen && "italic")}>{isOpen ? "Open shift" : target.employee}</SheetTitle>
        {isOpen ? (
          <Badge variant="outline">Unassigned</Badge>
        ) : (
          <Badge variant={target.shift.draft ? "outline" : "secondary"}>{target.shift.draft ? "Draft" : "Published"}</Badge>
        )}
      </div>
      <SheetDescription>
        {dayLabel(shift.date)} · {timeText(shift)}
      </SheetDescription>
    </SheetHeader>
  );
  if (past) {
    return (
      <>
        {header}
        <p className="px-4 text-sm text-muted-foreground">Today&apos;s and earlier shifts can&apos;t be changed from here.</p>
      </>
    );
  }
  const retimed = times.startMin !== shift.startMin || times.endMin !== shift.endMin;
  const to = isOpen ? employee : employee !== target.employee ? employee : null;
  const remove: ShiftChange = isOpen ? { kind: "open-delete", slot: target.slot, ...base } : { kind: "shift-delete", employee: target.employee, ...base };
  // ADP can't retime and hand over a shift in one step, so both at once is delete + a new shift.
  const changes: ShiftChange[] =
    to && retimed
      ? [remove, { kind: "shift-add", date: shift.date, employee: to, ...times }]
      : to
        ? [
            isOpen
              ? { kind: "open-assign", slot: target.slot, ...base, employee: to }
              : { kind: "shift-reassign", employee: target.employee, ...base, toEmployee: to },
          ]
        : retimed
          ? [isOpen ? { kind: "open-edit", slot: target.slot, ...base, to: times } : { kind: "shift-edit", employee: target.employee, ...base, to: times }]
          : [];
  const people = isOpen ? target.options : roster;
  const err = firstError(changes, "Change the times or the person.");
  return (
    <>
      {header}
      <div className="flex flex-col gap-4 px-4">
        <ShiftTimes value={times} onChange={setTimes} />
        {people.length ? (
          <PersonSelect value={employee} roster={people} label={isOpen ? "Assign to" : "Person"} onChange={setEmployee} />
        ) : (
          <p className="text-sm text-muted-foreground">Nobody fits your rules for this shift.</p>
        )}
        <p className="text-xs text-muted-foreground">
          {to && !isOpen
            ? `ADP can't hand an assigned shift to someone else, so this deletes it and adds it for ${to}. `
            : isOpen
              ? "Only people your scheduling rules allow are listed. "
              : ""}
          Nothing changes in ADP until you press Apply to ADP.
        </p>
      </div>
      <SheetFooter className="flex-row items-center">
        <Button variant="destructive" className="mr-auto" onClick={() => stageAll([remove])}>
          Delete
        </Button>
        <Button disabled={err != null} title={err ?? undefined} onClick={() => stageAll(changes)}>
          Save to changes
        </Button>
      </SheetFooter>
    </>
  );
}
