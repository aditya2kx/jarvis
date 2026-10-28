"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import type { UnavailabilityRow } from "@/lib/bq/queries";
import { bulkAdds, entryError, onOrAfter, type ChangeScope, type EntryFields, type StagedChange } from "@/lib/labor/availability-changes";
import { minToTime } from "@/lib/labor/schedule-inputs";
import { coversDate, type ScheduledShift } from "@/lib/labor/unavailability";
import { addDays, dayLabel, hoursText, shortDate, WEEKDAYS, whenText } from "@/lib/labor/unavailability-format";
import { cn } from "@/lib/utils";

export type SheetTarget =
  /** fromMin: where the operator tapped a lane; starts a two-hour block there instead of all day. */
  | { mode: "add"; employee: string | null; date: string; fromMin?: number }
  | { mode: "entry"; row: UnavailabilityRow; date: string };

const fromRule = (r: UnavailabilityRow) => r.row_key.startsWith("rule|");

function fieldsFor(target: SheetTarget): EntryFields {
  if (target.mode === "add") {
    const at = target.fromMin;
    return at == null
      ? { date: target.date, allDay: true, from: "09:00", to: "17:00", weekly: false, until: null }
      : { date: target.date, allDay: false, from: minToTime(at), to: minToTime(Math.min(at + 120, 23 * 60 + 45)), weekly: false, until: null };
  }
  const r = target.row;
  return {
    date: r.repeat_weekday != null ? target.date : r.first_date,
    allDay: r.all_day || !r.from_time || !r.to_time,
    from: r.from_time?.slice(0, 5) ?? "09:00",
    to: r.to_time?.slice(0, 5) ?? "17:00",
    weekly: r.repeat_weekday != null,
    until: r.repeat_until,
  };
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
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="unavail-from">From</Label>
            <Input id="unavail-from" type="time" step={900} value={fields.from ?? ""} onChange={(e) => set({ from: e.target.value })} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="unavail-to">To</Label>
            <Input id="unavail-to" type="time" step={900} value={fields.to ?? ""} onChange={(e) => set({ to: e.target.value })} />
          </div>
        </div>
      ) : null}
    </>
  );
}

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

function EntryForm({ fields, onChange }: { fields: EntryFields; onChange: (f: EntryFields) => void }) {
  const set = (patch: Partial<EntryFields>) => onChange({ ...fields, ...patch });
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="unavail-date">{fields.weekly ? "Starts" : "Date"}</Label>
        <Input id="unavail-date" type="date" value={fields.date} onChange={(e) => set({ date: e.target.value })} />
      </div>
      <TimeFields fields={fields} onChange={onChange} />
      <label className="flex items-center gap-2 text-sm">
        <Checkbox checked={fields.weekly} onCheckedChange={(v) => set({ weekly: Boolean(v) })} />
        Repeats weekly
      </label>
      {fields.weekly ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="unavail-until">Until (optional)</Label>
          <Input
            id="unavail-until"
            type="date"
            value={fields.until ?? ""}
            min={fields.date}
            onChange={(e) => set({ until: e.target.value || null })}
          />
        </div>
      ) : null}
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
  onClose,
  onStage,
}: {
  target: SheetTarget | null;
  roster: string[];
  todayIso: string;
  /** Existing unavailability + scheduled shifts, marked on the add drawer's day picker. */
  offRows?: UnavailabilityRow[];
  shifts?: ScheduledShift[];
  onClose: () => void;
  onStage: (c: StagedChange) => void;
}) {
  return (
    <Sheet open={target != null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-md">
        {target ? (
          <SheetBody key={target.mode === "add" ? `add-${target.employee}-${target.date}-${target.fromMin ?? ""}` : `${target.row.row_key}-${target.date}`} target={target} roster={roster} todayIso={todayIso} offRows={offRows} shifts={shifts} onClose={onClose} onStage={onStage} />
        ) : null}
      </SheetContent>
    </Sheet>
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
  target: SheetTarget;
  roster: string[];
  todayIso: string;
  offRows: UnavailabilityRow[];
  shifts: ScheduledShift[];
  onClose: () => void;
  onStage: (c: StagedChange) => void;
}) {
  const [fields, setFields] = useState<EntryFields>(() => fieldsFor(target));
  const [employee, setEmployee] = useState<string | null>(target.mode === "add" ? target.employee : target.row.employee);
  const [editing, setEditing] = useState(false);
  const [scope, setScope] = useState<ChangeScope>("series");
  // A lane tap names the person and day; the panel's Add button starts blank.
  const fromLane = target.mode === "add" && target.employee != null;
  const [days, setDays] = useState<string[]>(() => (fromLane ? [target.date] : []));
  const [repeat, setRepeat] = useState<"days" | "weekly">("days");
  const [weekdays, setWeekdays] = useState<number[]>(() => (fromLane ? [weekdayOf(target.date)] : []));
  const stageAndClose = (c: StagedChange) => {
    onStage(c);
    onClose();
  };

  if (target.mode === "add") {
    const marks = (iso: string): DayMark | null => {
      if (!employee) return null;
      const off = offRows.filter((r) => r.employee === employee && coversDate(r, iso));
      if (off.some((r) => r.status === "approved")) return "off";
      if (off.length) return "requested";
      return shifts.some((x) => x.employee === employee && x.date === iso) ? "shift" : null;
    };
    const count = repeat === "days" ? days.length : weekdays.length;
    const err = !employee
      ? "Pick a person."
      : !count
        ? repeat === "days"
          ? "Pick at least one day."
          : "Pick at least one weekday."
        : entryError(fields);
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
          <Segmented
            label="Which days"
            value={repeat}
            onChange={setRepeat}
            options={[
              ["days", "Pick days"],
              ["weekly", "Repeats weekly"],
            ]}
          />
          {repeat === "days" ? (
            <DayPicker start={target.date < todayIso ? todayIso : target.date} todayIso={todayIso} days={days} marks={marks} onChange={setDays} />
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
                        onClick={() => setWeekdays(on ? weekdays.filter((x) => x !== i) : [...weekdays, i])}
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
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="unavail-start">Starts</Label>
                  <Input id="unavail-start" type="date" min={todayIso} value={fields.date} onChange={(e) => setFields({ ...fields, date: e.target.value })} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="unavail-until">Until (optional)</Label>
                  <Input
                    id="unavail-until"
                    type="date"
                    min={fields.date}
                    value={fields.until ?? ""}
                    onChange={(e) => setFields({ ...fields, until: e.target.value || null })}
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
          <TimeFields fields={fields} onChange={setFields} />
        </div>
        <SheetFooter className="flex-row justify-end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={err != null}
            title={err ?? undefined}
            onClick={() => {
              const summarize = (f: EntryFields) => entrySummary(employee!, f);
              const pick = repeat === "days" ? { days } : { weekdays };
              for (const c of bulkAdds(employee!, fields, pick, summarize)) onStage(c);
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
  const scopeDate = scope === "date" ? target.date : r.first_date;
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

  const err = entryError(fields);
  return (
    <>
      {header}
      <div className="flex flex-col gap-4 px-4">
        {!editing ? (
          <div className="flex flex-col gap-3 text-sm">
            <p className="text-muted-foreground">
              Approved in ADP. <span className="text-foreground">Edit</span> changes the hours or dates;{" "}
              <span className="text-foreground">Delete</span> removes it from ADP. Either waits with your other changes until you apply.
            </p>
            {weekly ? (
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-muted-foreground">Next dates</span>
                <span className="tabular-nums">{nextDates(r, target.date).map(shortDate).join(" · ") || "None left"}</span>
              </div>
            ) : null}
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
        {editing ? (
          <EntryForm
            fields={scope === "date" ? { ...fields, date: target.date, weekly: false, until: null } : fields}
            onChange={setFields}
          />
        ) : null}
      </div>
      <SheetFooter className="flex-row justify-end">
        <Button
          variant="destructive"
          onClick={() =>
            stageAndClose({
              kind: "delete",
              rowKey: r.row_key,
              employee: r.employee,
              scope: weekly ? scope : "series",
              date: scopeDate,
              summary: `${r.employee} · ${scope === "date" && weekly ? `${dayLabel(target.date)} only` : whenText(r)}`,
            })
          }
        >
          Delete
        </Button>
        {editing ? (
          <Button
            disabled={err != null}
            title={err ?? undefined}
            onClick={() => {
              const f = scope === "date" && weekly ? { ...fields, date: target.date, weekly: false, until: null } : fields;
              stageAndClose({ kind: "edit", rowKey: r.row_key, employee: r.employee, scope: weekly ? scope : "series", summary: entrySummary(r.employee, f), ...f });
            }}
          >
            Save to changes
          </Button>
        ) : (
          <Button variant="outline" onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
      </SheetFooter>
    </>
  );
}
