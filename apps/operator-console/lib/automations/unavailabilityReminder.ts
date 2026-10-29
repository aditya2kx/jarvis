/** Pure compose + flag helpers — mirror agents/bhaga/scripts/unavailability_reminder.py (Issues #381, #435). */

import { coversDate, type UnavailabilityInput } from "@/lib/labor/unavailability";

export const REMINDER_AUTOMATION_ID = "unavailability-reminder";
export const REMINDER_DEFAULT_DAYS = [2, 3]; // Wed Thu (Python weekday Mon=0)
export const REMINDER_DEFAULT_HOUR = 10;
export const REMINDER_DEFAULT_LEAD_WEEKS = 4;
export const REMINDER_MIN_LEAD_WEEKS = 2;
export const REMINDER_MAX_LEAD_WEEKS = 8;
/** Private #jarvis-drafts — only the operator is a member. */
export const REMINDER_DRAFTS_CHANNEL_ID = "8cr6661-7217";
const PUBLISH_WEEKDAY = 4; // Friday

export const REMINDER_DEFAULT_TEMPLATE =
  "@everyone Friendly reminder to update unavailability on ADP. Schedules through {through_date} " +
  "will be published this {publish_day} based entirely off your ADP unavailability. " +
  "Please keep it updated at least {lead_weeks} weeks in advance to avoid swapping shifts after that.";
export const REMINDER_DEFAULT_FOLLOWUP =
  "{flagged} One last reminder before shifts through {through_date} get published tomorrow. " +
  "Please keep your ADP unavailability updated at least {lead_weeks} weeks ahead.";

export type ReminderKind = "reminder" | "followup";

const HEADERS: Record<ReminderKind, string> = {
  reminder: "Unavailability reminder for Shift Coverage & Trades — copy and post:",
  followup: "Unavailability follow-up — post as a reply in this week's reminder thread:",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function parseIso(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getTime() + n * 86_400_000);
}

/** Python weekday (Mon=0 … Sun=6) of a UTC calendar date. */
function pyWeekday(d: Date): number {
  return (d.getUTCDay() + 6) % 7;
}

function md(d: Date): string {
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function mondayOf(todayIso: string): Date {
  const today = parseIso(todayIso);
  return addDays(today, -pyWeekday(today));
}

export function reminderKind(todayIso: string, days: number[]): ReminderKind {
  return !days.length || pyWeekday(parseIso(todayIso)) <= Math.min(...days) ? "reminder" : "followup";
}

export function clampLeadWeeks(n: number | null | undefined): number {
  const v = n == null || !Number.isFinite(n) ? REMINDER_DEFAULT_LEAD_WEEKS : Math.round(n);
  return Math.min(REMINDER_MAX_LEAD_WEEKS, Math.max(REMINDER_MIN_LEAD_WEEKS, v));
}

/** Mondays of the weeks this Friday's publish can add: the week after next through the lead week. */
export function checkWeeks(todayIso: string, leadWeeks: number): string[] {
  const monday = mondayOf(todayIso);
  const out: string[] = [];
  for (let w = 2; w <= leadWeeks; w++) out.push(iso(addDays(monday, 7 * w)));
  return out;
}

/** People on `roster` with at least one of `weeks` that has no unavailability on any day. */
export function unmarkedPeople(
  roster: string[],
  rows: Pick<UnavailabilityInput, "employee" | "first_date" | "repeat_weekday" | "repeat_until">[],
  weeks: string[],
): string[] {
  const byPerson = new Map<string, typeof rows>();
  for (const r of rows) byPerson.set(r.employee, [...(byPerson.get(r.employee) ?? []), r]);
  return [...new Set(roster)].sort().filter((person) => {
    const mine = (byPerson.get(person) ?? []) as UnavailabilityInput[];
    return weeks.some((monday) => {
      const start = parseIso(monday);
      return ![0, 1, 2, 3, 4, 5, 6].some((i) => mine.some((r) => coversDate(r, iso(addDays(start, i)))));
    });
  });
}

/**
 * The draft for `todayIso` (CT calendar date). `{target_week}` is the lead week (the one each
 * Friday adds), `{through_date}` its Sunday, `{flagged}` the mentions joined by spaces (dropped when empty).
 */
export function composeReminder(
  todayIso: string,
  days: number[],
  template: string,
  followup: string,
  leadWeeks: number = REMINDER_DEFAULT_LEAD_WEEKS,
  flagged: string[] = [],
): { kind: ReminderKind; content: string } {
  const monday = mondayOf(todayIso);
  const target = addDays(monday, 7 * leadWeeks);
  const publish = addDays(monday, PUBLISH_WEEKDAY);
  const kind = reminderKind(todayIso, days);
  const tags = flagged.join(" ");
  let text = kind === "reminder" ? template : followup;
  if (!tags) text = text.split("{flagged} ").join("").split(" {flagged}").join("");
  const body = text
    .split("{flagged}").join(tags)
    .split("{target_week}").join(`${md(target)} - ${md(addDays(target, 6))}`)
    .split("{through_date}").join(md(addDays(target, 6)))
    .split("{publish_day}").join(`${WEEKDAYS[pyWeekday(publish)]}(${md(publish)})`)
    .split("{lead_weeks}").join(String(leadWeeks))
    .trim();
  return { kind, content: `${HEADERS[kind]}\n\n---\n\n${body}` };
}
