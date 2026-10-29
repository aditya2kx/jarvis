/**
 * Every change the operator stages for ADP from Labor › Staffing coverage (Issue #434): shift
 * changes plus the unavailability changes from Issue #427, applied together in one ADP run.
 * ADP stays the source of truth — a staged change is only an intent until the run reads it back.
 */
import { changeKey, entryError, KIND_LABEL as UNAVAIL_LABEL, trayGroups, type StagedChange, type TrayGroup } from "@/lib/labor/availability-changes";
import { dayLabel, hoursText, shortDate, weekdaysText } from "@/lib/labor/unavailability-format";
import { formatClockMin } from "@/lib/labor/coverage-model";
import { parseShiftRange } from "@/lib/labor/shift-ranges";

export type Times = { startMin: number; endMin: number };

/**
 * `date` / `startMin` / `endMin` are always the shift as ADP has it now (for adds, the new shift);
 * `to` / `toEmployee` are what it becomes. `slot` identifies one open shift on the client only.
 */
export type ShiftChange =
  | ({ kind: "open-add"; date: string; count: number } & Times)
  | ({ kind: "open-assign"; slot: string; date: string; employee: string } & Times)
  | ({ kind: "open-edit"; slot: string; date: string; to: Times } & Times)
  | ({ kind: "open-delete"; slot: string; date: string } & Times)
  | ({ kind: "shift-add"; date: string; employee: string } & Times)
  | ({ kind: "shift-edit"; date: string; employee: string; to: Times } & Times)
  | ({ kind: "shift-delete"; date: string; employee: string } & Times)
  /** A draft ADP already has, made live; the run deletes the draft and adds the same shift published. */
  | ({ kind: "shift-publish"; date: string; employee: string } & Times)
  /** ADP can't reassign an assigned shift: the run deletes it, then adds the same hours for `toEmployee`. */
  | ({ kind: "shift-reassign"; date: string; employee: string; toEmployee: string } & Times);

export type AdpChange = { area: "shift"; c: ShiftChange } | { area: "unavailability"; c: StagedChange };

export const MAX_CHANGES = 80;
const MAX_SHIFT_MIN = 16 * 60;
const MAX_OPEN_COUNT = 10;
const ISO = /^\d{4}-\d{2}-\d{2}$/;

const span = (t: Times) => `${t.startMin}-${t.endMin}`;

/** One staged change per ADP shift: editing then deleting the same shift keeps only the delete. */
export function shiftKey(c: ShiftChange): string {
  switch (c.kind) {
    case "open-add":
      return `open-add|${c.date}|${span(c)}`;
    case "open-assign":
    case "open-edit":
    case "open-delete":
      return `open|${c.slot}`;
    case "shift-add":
      return `shift-add|${c.employee}|${c.date}|${span(c)}`;
    case "shift-edit":
    case "shift-delete":
    case "shift-reassign":
    case "shift-publish":
      return `shift|${c.employee}|${c.date}|${span(c)}`;
    default: {
      const never: never = c;
      return never;
    }
  }
}

export function adpChangeKey(a: AdpChange): string {
  return a.area === "shift" ? `s:${shiftKey(a.c)}` : `u:${changeKey(a.c)}`;
}

export function stageAdp(queue: AdpChange[], change: AdpChange): AdpChange[] {
  const key = adpChangeKey(change);
  return [...queue.filter((c) => adpChangeKey(c) !== key), change];
}

export function unstageAdp(queue: AdpChange[], keys: string[]): AdpChange[] {
  const drop = new Set(keys);
  return queue.filter((c) => !drop.has(adpChangeKey(c)));
}

/** The staged change for an existing shift (assigned) or open shift (by slot), if any. */
export function stagedShift(queue: AdpChange[], key: string): ShiftChange | undefined {
  const hit = queue.find((a) => a.area === "shift" && shiftKey(a.c) === key);
  return hit?.area === "shift" ? hit.c : undefined;
}

export const timeText = (t: Times) => `${formatClockMin(t.startMin)}–${formatClockMin(t.endMin)}`;

/** What the change does, in one line (also the history summary the run records). */
export function shiftText(c: ShiftChange): string {
  const when = `${dayLabel(c.date)} · ${timeText(c)}`;
  switch (c.kind) {
    case "open-add":
      return `Open shift · ${when}${c.count > 1 ? ` · ${c.count} of them` : ""}`;
    case "open-assign":
      return `Open shift · ${when} → ${c.employee}`;
    case "open-edit":
      return `Open shift · ${when} → ${timeText(c.to)}`;
    case "open-delete":
      return `Open shift · ${when}`;
    case "shift-add":
    case "shift-delete":
    case "shift-publish":
      return `${c.employee} · ${when}`;
    case "shift-edit":
      return `${c.employee} · ${when} → ${timeText(c.to)}`;
    case "shift-reassign":
      return `${when} · ${c.employee} → ${c.toEmployee}`;
    default: {
      const never: never = c;
      return never;
    }
  }
}

export const SHIFT_LABEL: Record<ShiftChange["kind"], string> = {
  "open-add": "Add",
  "open-assign": "Assign",
  "open-edit": "Edit",
  "open-delete": "Delete",
  "shift-add": "Add",
  "shift-edit": "Edit",
  "shift-delete": "Delete",
  "shift-reassign": "Reassign",
  "shift-publish": "Publish",
};

function timesError(t: Times, what: string): string | null {
  const ok = Number.isInteger(t.startMin) && Number.isInteger(t.endMin) && t.startMin >= 0 && t.endMin <= 24 * 60;
  if (!ok) return `Pick a ${what} start and end time.`;
  if (t.endMin <= t.startMin) return "End time must be after the start time.";
  if (t.endMin - t.startMin > MAX_SHIFT_MIN) return "A shift can be at most 16 hours.";
  return null;
}

/** Shifts change from tomorrow on (today's are under way); null when the change is fine to stage. */
export function shiftError(c: ShiftChange, todayIso: string): string | null {
  if (!ISO.test(c.date)) return "Pick a date.";
  if (c.date <= todayIso) return "Shifts can only change from tomorrow on.";
  const own = timesError(c, "shift");
  if (own) return own;
  switch (c.kind) {
    case "open-add":
      return Number.isInteger(c.count) && c.count >= 1 && c.count <= MAX_OPEN_COUNT
        ? null
        : `Add 1 to ${MAX_OPEN_COUNT} open shifts at a time.`;
    case "open-edit":
    case "shift-edit":
      return timesError(c.to, "new") ?? (span(c.to) === span(c) ? "Change the start or end time." : null);
    case "open-assign":
    case "shift-add":
    case "shift-delete":
    case "shift-publish":
    case "open-delete":
      return "employee" in c && !c.employee.trim() ? "Pick a person." : null;
    case "shift-reassign":
      if (!c.toEmployee.trim()) return "Pick who takes it instead.";
      return c.toEmployee === c.employee ? "Pick someone else." : null;
    default: {
      const never: never = c;
      return never;
    }
  }
}

const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
const int = (v: unknown) => (typeof v === "number" && Number.isInteger(v) ? v : NaN);
const times = (raw: Record<string, unknown>): Times => ({ startMin: int(raw.startMin), endMin: int(raw.endMin) });

/** Rebuilds one shift change from untrusted input, keeping only the fields its kind has. */
function parseShift(raw: Record<string, unknown>): ShiftChange {
  const base = { date: str(raw.date), ...times(raw) };
  const to = raw.to && typeof raw.to === "object" ? times(raw.to as Record<string, unknown>) : { startMin: NaN, endMin: NaN };
  const kind = raw.kind;
  switch (kind) {
    case "open-add":
      return { kind, ...base, count: int(raw.count) };
    case "open-assign":
      return { kind, slot: "", ...base, employee: str(raw.employee) };
    case "open-edit":
      return { kind, slot: "", ...base, to };
    case "open-delete":
      return { kind, slot: "", ...base };
    case "shift-add":
    case "shift-delete":
    case "shift-publish":
      return { kind, ...base, employee: str(raw.employee) };
    case "shift-edit":
      return { kind, ...base, employee: str(raw.employee), to };
    case "shift-reassign":
      return { kind, ...base, employee: str(raw.employee), toEmployee: str(raw.toEmployee) };
    default:
      throw new Error("Unknown shift change.");
  }
}

const UNAVAIL_KINDS = new Set<StagedChange["kind"]>(["approve", "deny", "add", "edit", "delete"]);

function parseUnavailability(raw: Record<string, unknown>, todayIso: string): StagedChange {
  const kind = raw.kind as StagedChange["kind"];
  if (!UNAVAIL_KINDS.has(kind)) throw new Error("Unknown unavailability change.");
  const employee = str(raw.employee);
  const summary = str(raw.summary).slice(0, 300);
  if (!employee) throw new Error("Pick a person.");
  const rowKey = str(raw.rowKey);
  if (kind !== "add" && !rowKey) throw new Error("Missing the ADP entry.");
  if (kind === "approve" || kind === "deny") return { kind, rowKey, employee, summary };
  const scope = raw.scope === "date" ? "date" : "series";
  if (kind === "delete") {
    const date = str(raw.date);
    if (!ISO.test(date)) throw new Error("Pick a date.");
    return { kind, rowKey, employee, scope, date, summary };
  }
  const fields = {
    date: str(raw.date),
    allDay: raw.allDay === true,
    from: str(raw.from) || null,
    to: str(raw.to) || null,
    weekly: raw.weekly === true,
    until: str(raw.until) || null,
  };
  const err = entryError(fields);
  if (err) throw new Error(err);
  if (fields.date < todayIso && !fields.weekly) throw new Error("Unavailability can't start in the past.");
  return kind === "add" ? { kind, employee, summary, ...fields } : { kind, rowKey, employee, scope, summary, ...fields };
}

/** Server-side check of an Apply: throws a toast-ready message naming the change that's wrong. */
export function validateAdpChanges(raw: unknown, todayIso: string): AdpChange[] {
  if (!Array.isArray(raw) || raw.length === 0) throw new Error("Nothing to apply.");
  if (raw.length > MAX_CHANGES) throw new Error(`Too many changes (${raw.length}); apply at most ${MAX_CHANGES} at once.`);
  const seen = new Set<string>();
  return raw.map((item, i) => {
    const a = (item ?? {}) as { area?: unknown; c?: unknown };
    const c = (a.c ?? {}) as Record<string, unknown>;
    try {
      let out: AdpChange;
      if (a.area === "shift") {
        const s = parseShift(c);
        const err = shiftError(s, todayIso);
        if (err) throw new Error(err);
        out = { area: "shift", c: s };
      } else if (a.area === "unavailability") {
        out = { area: "unavailability", c: parseUnavailability(c, todayIso) };
      } else {
        throw new Error("Unknown change.");
      }
      // Open shifts lose their client slot here, so two identical open shifts stay two rows.
      const key = out.area === "shift" && "slot" in out.c ? `${adpChangeKey(out)}|${i}` : adpChangeKey(out);
      if (seen.has(key)) throw new Error("The same change is listed twice.");
      seen.add(key);
      return out;
    } catch (e) {
      throw new Error(`Change ${i + 1}: ${e instanceof Error ? e.message : String(e)}`);
    }
  });
}

export type TrayLine = {
  key: string;
  area: AdpChange["area"];
  label: string;
  text: string;
  destructive: boolean;
  /** Queue keys this line undoes. */
  keys: string[];
};

/** A person's same-hours unavailability adds fold into one line ("3 days"). */
export function unavailabilityText(g: TrayGroup): string {
  const adds = g.changes.flatMap((c) => (c.kind === "add" ? [c] : []));
  if (adds.length < 2) return g.changes[0]!.summary;
  const a = adds[0]!;
  const hours = hoursText({ all_day: a.allDay, from_time: a.from, to_time: a.to });
  if (!a.weekly) return `${g.employee} · ${adds.length} days · ${adds.map((c) => shortDate(c.date)).join(", ")} · ${hours}`;
  const wds = adds.map((c) => (new Date(`${c.date}T12:00:00Z`).getUTCDay() + 6) % 7).sort((x, y) => x - y);
  return `${g.employee} · Every ${weekdaysText(wds)} · ${hours}${a.until ? ` · until ${shortDate(a.until)}` : ""}`;
}

/**
 * The tray: shift lines by day, then unavailability by person. Adds of the same hours for the
 * same person (or open shifts of the same hours and count) fold into one line across days.
 */
export function trayLines(queue: AdpChange[]): TrayLine[] {
  const shifts = new Map<string, { kind: ShiftChange["kind"]; changes: ShiftChange[] }>();
  const unavail: StagedChange[] = [];
  for (const a of queue) {
    if (a.area === "unavailability") {
      unavail.push(a.c);
      continue;
    }
    const c = a.c;
    const group =
      c.kind === "shift-add" || c.kind === "shift-publish"
        ? `${c.kind}|${c.employee}|${span(c)}`
        : c.kind === "open-add"
          ? `open-add|${span(c)}|${c.count}`
          : shiftKey(c);
    const g = shifts.get(group) ?? { kind: c.kind, changes: [] };
    g.changes.push(c);
    shifts.set(group, g);
  }
  const firstDate = new Map<string, string>();
  const shiftLines: TrayLine[] = [...shifts.entries()].map(([key, g]) => {
    const list = [...g.changes].sort((x, y) => x.date.localeCompare(y.date));
    const first = list[0]!;
    firstDate.set(`s:${key}`, first.date);
    const who = first.kind === "open-add" ? "Open shift" : "employee" in first ? first.employee : "";
    const count = first.kind === "open-add" && first.count > 1 ? ` · ${first.count} each day` : "";
    return {
      key: `s:${key}`,
      area: "shift",
      label: SHIFT_LABEL[g.kind],
      text:
        list.length > 1
          ? `${who} · ${list.length} days · ${list.map((c) => shortDate(c.date)).join(", ")} · ${timeText(first)}${count}`
          : shiftText(first),
      destructive: g.kind === "open-delete" || g.kind === "shift-delete",
      keys: list.map((c) => `s:${shiftKey(c)}`),
    };
  });
  shiftLines.sort((x, y) => firstDate.get(x.key)!.localeCompare(firstDate.get(y.key)!) || x.text.localeCompare(y.text));
  const unavailLines = trayGroups(unavail).map((g) => ({
    key: `u:${g.key}`,
    area: "unavailability" as const,
    label: UNAVAIL_LABEL[g.kind],
    text: unavailabilityText(g),
    destructive: g.kind === "deny" || g.kind === "delete",
    keys: g.changes.map((c) => `u:${changeKey(c)}`),
  }));
  return [...shiftLines, ...unavailLines];
}

/** Who a change is about, for the tray's "N people" count (open shifts count as nobody). */
export function changePeople(queue: AdpChange[]): number {
  const people = new Set<string>();
  for (const a of queue) {
    if (a.area === "unavailability") people.add(a.c.employee);
    else {
      if ("employee" in a.c) people.add(a.c.employee);
      if (a.c.kind === "shift-reassign") people.add(a.c.toEmployee);
    }
  }
  return people.size;
}

/**
 * A stored queue against the ADP read now on the page: changes to days that have passed, or to
 * entries / shifts ADP no longer shows, are dropped (they can't apply any more).
 */
export function pruneQueue(
  queue: AdpChange[],
  now: {
    todayIso: string;
    unavailabilityKeys: ReadonlySet<string>;
    openSlots: ReadonlySet<string>;
    hasShift: (employee: string, date: string, t: Times) => boolean;
  },
): AdpChange[] {
  return queue.filter((a) => {
    if (a.area === "unavailability") {
      const c = a.c;
      if (c.kind === "add") return c.weekly || c.date >= now.todayIso;
      return now.unavailabilityKeys.has(c.rowKey);
    }
    const c = a.c;
    if (c.date <= now.todayIso) return false;
    switch (c.kind) {
      case "open-add":
      case "shift-add":
        return true;
      case "open-assign":
      case "open-edit":
      case "open-delete":
        return now.openSlots.has(c.slot);
      case "shift-edit":
      case "shift-delete":
      case "shift-reassign":
      case "shift-publish":
        return now.hasShift(c.employee, c.date, c);
      default: {
        const never: never = c;
        return never;
      }
    }
  });
}

/** Parsed localStorage queue; anything unreadable is dropped rather than half-applied. */
export function readStoredQueue(raw: string | null): AdpChange[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (a): a is AdpChange =>
        a != null &&
        typeof a === "object" &&
        (a.area === "shift" || a.area === "unavailability") &&
        a.c != null &&
        typeof a.c === "object" &&
        typeof a.c.kind === "string",
    );
  } catch {
    return [];
  }
}

export type PersonShift = { date: string; draft: boolean } & Times;

/** One person's ADP shifts on or after `fromIso`, soonest first; `draft` = ADP still shows it as a draft. */
export function personShifts(
  shifts: { date: string; employee: string; shift_ranges_json: string | null }[],
  employee: string,
  fromIso: string,
): PersonShift[] {
  const out: PersonShift[] = [];
  for (const s of shifts) {
    if (s.employee !== employee || s.date < fromIso) continue;
    let ranges: unknown;
    try {
      ranges = JSON.parse(s.shift_ranges_json ?? "[]");
    } catch {
      continue;
    }
    if (!Array.isArray(ranges)) continue;
    for (const r of ranges) {
      const t = parseShiftRange(typeof r === "string" ? r : null);
      if (t) out.push({ date: s.date, startMin: t.startMin, endMin: t.endMin, draft: /draft/i.test(String(r)) });
    }
  }
  return out.sort((a, b) => a.date.localeCompare(b.date) || a.startMin - b.startMin);
}

export type WeekShift = { date: string; employee: string } & Times;

/** Assigned shifts ADP still shows as drafts, dated after `afterIso` up to `throughIso`. */
export function adpDrafts(
  shifts: { date: string; employee: string; shift_ranges_json: string | null }[],
  afterIso: string,
  throughIso: string,
): WeekShift[] {
  const people = new Set(shifts.filter((s) => s.date > afterIso && s.date <= throughIso).map((s) => s.employee));
  return [...people].flatMap((employee) =>
    personShifts(shifts, employee, afterIso)
      .filter((p) => p.draft && p.date > afterIso && p.date <= throughIso)
      .map(({ date, startMin, endMin }) => ({ date, employee, startMin, endMin })),
  );
}

/**
 * The suggested week as tray changes: suggested shifts ADP doesn't have become adds (open shifts at
 * the same day and hours fold into one count), drafts ADP already has become publishes.
 */
export function suggestedChanges(
  suggested: ({ date: string; employee: string | null; inAdp?: boolean } & Times)[],
  drafts: WeekShift[],
  todayIso: string,
): ShiftChange[] {
  const out: ShiftChange[] = [];
  const open = new Map<string, Extract<ShiftChange, { kind: "open-add" }>>();
  for (const s of suggested) {
    if (s.inAdp || s.date <= todayIso) continue;
    const t = { startMin: s.startMin, endMin: s.endMin };
    if (s.employee) {
      out.push({ kind: "shift-add", date: s.date, employee: s.employee, ...t });
      continue;
    }
    const slot = open.get(`${s.date}|${span(t)}`);
    if (slot) slot.count += 1;
    else {
      const c = { kind: "open-add" as const, date: s.date, count: 1, ...t };
      open.set(`${s.date}|${span(t)}`, c);
      out.push(c);
    }
  }
  for (const d of drafts) if (d.date > todayIso) out.push({ kind: "shift-publish", ...d });
  return out.sort((a, b) => a.date.localeCompare(b.date) || a.startMin - b.startMin);
}

/** "HH:MM" ↔ minutes for the drawers' time inputs. */
export const toMin = (hhmm: string) => (/^\d{2}:\d{2}/.test(hhmm) ? Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5)) : NaN);
export const toHhmm = (min: number) => `${String(Math.floor(min / 60) % 24).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

export type ApplyRowStatus = "queued" | "applied" | "skipped" | "failed";

/** One row of an Apply (shift or unavailability), as the run reports it. */
export type ApplyRow = {
  change_id: string;
  area: AdpChange["area"];
  summary: string | null;
  status: ApplyRowStatus;
  error: string | null;
};

export type ApplySummary = Record<ApplyRowStatus, number> & { errors: string[] };

export function summarizeApply(rows: ApplyRow[]): ApplySummary {
  const out: ApplySummary = { queued: 0, applied: 0, skipped: 0, failed: 0, errors: [] };
  for (const r of rows) {
    out[r.status] += 1;
    if (r.status === "failed") out.errors.push(`${r.summary ?? "Change"}: ${r.error ?? "not confirmed in ADP"}`);
  }
  return out;
}
