/**
 * Unavailability changes staged in the console and applied to ADP together
 * (Issue #427). ADP stays the source of truth: a staged change is only an
 * intent until a read-back of ADP shows it.
 */

/** Why Apply is unavailable, or null when it can run. */
export type ApplyBlock = "preview" | "off" | null;

/** "date" = only the selected day of a weekly entry; "series" = every date it repeats. */
export type ChangeScope = "date" | "series";

export type EntryFields = {
  /** First (or only) date, YYYY-MM-DD. */
  date: string;
  allDay: boolean;
  /** HH:MM, 24 h; null when allDay. */
  from: string | null;
  to: string | null;
  weekly: boolean;
  /** Last date of a weekly repeat; null = no end date. */
  until: string | null;
};

export type StagedChange =
  | { kind: "approve" | "deny"; rowKey: string; employee: string; summary: string }
  | ({ kind: "add"; employee: string; summary: string } & EntryFields)
  | ({ kind: "edit"; rowKey: string; employee: string; scope: ChangeScope; summary: string } & EntryFields)
  | { kind: "delete"; rowKey: string; employee: string; scope: ChangeScope; date: string; summary: string };

/** One staged change per ADP entry: approving then denying the same request keeps only the deny. */
export function changeKey(c: StagedChange): string {
  switch (c.kind) {
    case "approve":
    case "deny":
      return `req|${c.rowKey}`;
    case "edit":
    case "delete":
      return `entry|${c.rowKey}|${c.scope === "date" ? c.date : "series"}`;
    case "add":
      return `add|${c.employee}|${c.date}|${c.allDay ? "all" : `${c.from}-${c.to}`}|${c.weekly ? c.until ?? "open" : "once"}`;
    default: {
      const never: never = c;
      return never;
    }
  }
}

export function stage(queue: StagedChange[], change: StagedChange): StagedChange[] {
  const key = changeKey(change);
  return [...queue.filter((c) => changeKey(c) !== key), change];
}

export function unstage(queue: StagedChange[], key: string): StagedChange[] {
  return queue.filter((c) => changeKey(c) !== key);
}

/** Staged decision for a pending request, if any. */
export function stagedFor(queue: StagedChange[], rowKey: string): StagedChange | undefined {
  return queue.find((c) => "rowKey" in c && c.rowKey === rowKey);
}

export const KIND_LABEL: Record<StagedChange["kind"], string> = {
  approve: "Approve",
  deny: "Deny",
  add: "Add",
  edit: "Edit",
  delete: "Delete",
};

/** HH:MM ≥ from, same day; a weekly entry needs an end date on or after its start. */
export function entryError(f: EntryFields): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.date)) return "Pick a date.";
  if (!f.allDay) {
    if (!f.from || !f.to) return "Pick a start and end time, or turn on All day.";
    if (f.to <= f.from) return "End time must be after the start time.";
  }
  if (f.weekly && f.until && f.until < f.date) return "Repeat end date is before the first date.";
  return null;
}

/** First date on or after `iso` that falls on Mon=0 `weekday`. */
export function onOrAfter(iso: string, weekday: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  const wd = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() + ((weekday - wd + 7) % 7));
  return d.toISOString().slice(0, 10);
}

/**
 * One "add" per date (or per weekday for a weekly repeat), so ADP gets one entry each and
 * any one of them can be undone. `days` = picked dates; `weekdays` (Mon=0) = weekly repeat.
 */
export function bulkAdds(
  employee: string,
  f: EntryFields,
  pick: { days: string[] } | { weekdays: number[] },
  summarize: (f: EntryFields) => string,
): StagedChange[] {
  const fields =
    "days" in pick
      ? [...new Set(pick.days)].sort().map((date) => ({ ...f, date, weekly: false, until: null }))
      : [...new Set(pick.weekdays)].sort().map((wd) => ({ ...f, date: onOrAfter(f.date, wd), weekly: true }));
  return fields.map((x) => ({ kind: "add", employee, summary: summarize(x), ...x }));
}

export type TrayGroup = { key: string; kind: StagedChange["kind"]; employee: string; changes: StagedChange[] };

/**
 * The tray, one line per person and kind, people in name order: adds with the same hours fold
 * into one line ("3 days"). This is also the order the ADP run works through.
 */
export function trayGroups(queue: StagedChange[]): TrayGroup[] {
  const groups = new Map<string, TrayGroup>();
  for (const c of queue) {
    const key =
      c.kind === "add"
        ? `add|${c.employee}|${c.allDay ? "all" : `${c.from}-${c.to}`}|${c.weekly ? `w${c.until ?? ""}` : "d"}`
        : changeKey(c);
    const g = groups.get(key) ?? { key, kind: c.kind, employee: c.employee, changes: [] };
    g.changes.push(c);
    groups.set(key, g);
  }
  return [...groups.values()].sort((a, b) => a.employee.localeCompare(b.employee) || a.key.localeCompare(b.key));
}
