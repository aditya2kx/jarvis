/** Pure compose helpers — mirror agents/bhaga/scripts/unavailability_reminder.py (Issue #381). */

export const REMINDER_AUTOMATION_ID = "unavailability-reminder";
export const REMINDER_DEFAULT_DAYS = [2, 3]; // Wed Thu (Python weekday Mon=0)
export const REMINDER_DEFAULT_HOUR = 10;
const PUBLISH_WEEKDAY = 4; // Friday

export const REMINDER_DEFAULT_TEMPLATE =
  "@everyone Friendly reminder to update unavailability on ADP. Schedule for {target_week}, " +
  "will be published this {publish_day} based entirely off your ADP unavailability. " +
  "Please update it beforehand to avoid swapping shifts after that.";
export const REMINDER_DEFAULT_FOLLOWUP = "One last reminder before shifts get published tomorrow";

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

export function reminderKind(todayIso: string, days: number[]): ReminderKind {
  return !days.length || pyWeekday(parseIso(todayIso)) <= Math.min(...days) ? "reminder" : "followup";
}

/** The DM for `todayIso` (CT calendar date). The schedule being collected is the week after next. */
export function composeReminder(
  todayIso: string,
  days: number[],
  template: string,
  followup: string,
): { kind: ReminderKind; content: string } {
  const today = parseIso(todayIso);
  const monday = addDays(today, -pyWeekday(today));
  const target = addDays(monday, 14);
  const publish = addDays(monday, PUBLISH_WEEKDAY);
  const kind = reminderKind(todayIso, days);
  const body = (kind === "reminder" ? template : followup)
    .split("{target_week}").join(`${md(target)} - ${md(addDays(target, 6))}`)
    .split("{publish_day}").join(`${WEEKDAYS[pyWeekday(publish)]}(${md(publish)})`)
    .trim();
  return { kind, content: `${HEADERS[kind]}\n\n---\n\n${body}` };
}
