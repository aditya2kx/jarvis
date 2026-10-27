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
import { useIsPhone } from "@/components/shell/useIsPhone";
import type { UnavailabilityRow } from "@/lib/bq/queries";
import { entryError, type ChangeScope, type EntryFields, type StagedChange } from "@/lib/labor/availability-changes";
import { addDays } from "@/lib/labor/availability-week";
import { coversDate } from "@/lib/labor/unavailability";
import { dayLabel, hoursText, shortDate, WEEKDAYS, whenText } from "@/lib/labor/unavailability-format";
import { cn } from "@/lib/utils";

export type SheetTarget =
  | { mode: "add"; employee: string | null; date: string }
  | { mode: "entry"; row: UnavailabilityRow; date: string };

const fromRule = (r: UnavailabilityRow) => r.row_key.startsWith("rule|");

function fieldsFor(target: SheetTarget): EntryFields {
  if (target.mode === "add") {
    return { date: target.date, allDay: true, from: "09:00", to: "17:00", weekly: false, until: null };
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

function EntryForm({
  fields,
  onChange,
  employee,
  roster,
  onEmployee,
}: {
  fields: EntryFields;
  onChange: (f: EntryFields) => void;
  employee: string | null;
  roster?: string[];
  onEmployee?: (name: string) => void;
}) {
  const set = (patch: Partial<EntryFields>) => onChange({ ...fields, ...patch });
  return (
    <div className="flex flex-col gap-4">
      {roster && onEmployee ? (
        <div className="flex flex-col gap-1.5">
          <Label>Person</Label>
          <Select value={employee} onValueChange={(v) => v && onEmployee(String(v))}>
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
      ) : null}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="unavail-date">{fields.weekly ? "Starts" : "Date"}</Label>
        <Input id="unavail-date" type="date" value={fields.date} onChange={(e) => set({ date: e.target.value })} />
      </div>
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
  onClose,
  onStage,
}: {
  target: SheetTarget | null;
  roster: string[];
  onClose: () => void;
  onStage: (c: StagedChange) => void;
}) {
  const phone = useIsPhone();
  return (
    <Sheet open={target != null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side={phone ? "bottom" : "right"} className="max-md:max-h-[90dvh] max-md:overflow-y-auto sm:max-w-md">
        {target ? (
          <SheetBody key={target.mode === "add" ? `add-${target.employee}-${target.date}` : `${target.row.row_key}-${target.date}`} target={target} roster={roster} onClose={onClose} onStage={onStage} />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

function SheetBody({
  target,
  roster,
  onClose,
  onStage,
}: {
  target: SheetTarget;
  roster: string[];
  onClose: () => void;
  onStage: (c: StagedChange) => void;
}) {
  const [fields, setFields] = useState<EntryFields>(() => fieldsFor(target));
  const [employee, setEmployee] = useState<string | null>(target.mode === "add" ? target.employee : target.row.employee);
  const [editing, setEditing] = useState(false);
  const [scope, setScope] = useState<ChangeScope>("series");
  const stageAndClose = (c: StagedChange) => {
    onStage(c);
    onClose();
  };

  if (target.mode === "add") {
    const err = !employee ? "Pick a person." : entryError(fields);
    return (
      <>
        <SheetHeader>
          <SheetTitle>Add unavailability</SheetTitle>
          <SheetDescription>Staged with your other changes, then created in ADP when you apply.</SheetDescription>
        </SheetHeader>
        <div className="px-4">
          <EntryForm fields={fields} onChange={setFields} employee={employee} roster={roster} onEmployee={setEmployee} />
        </div>
        <SheetFooter className="flex-row justify-end">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={err != null}
            title={err ?? undefined}
            onClick={() => stageAndClose({ kind: "add", employee: employee!, summary: entrySummary(employee!, fields), ...fields })}
          >
            Add to changes
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
          Entered as an &ldquo;Unavailable on dates&rdquo; scheduling rule, not in ADP. Change it under Coverage &amp; shift draft
          › Scheduling rules.
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
            employee={employee}
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
