import { useState } from "react";
import type { Repeat } from "./api";
import { cx, Field, Input, inputClass } from "./ui";

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LAST_DAY = -1;
const MAX_EVERY_WEEKS = 4;

export const EVERY_DAY: Repeat = { weekdays: [], every_weeks: 1, month_days: [], start_date: null, end_date: null };

/** Monday = 0, as the service counts. */
export const weekdayOf = (date: string) => (new Date(`${date}T12:00:00`).getDay() + 6) % 7;

const ordinal = (n: number) => `${n}${["th", "st", "nd", "rd"][n % 100 > 10 && n % 100 < 14 ? 0 : Math.min(n % 10, 4) % 4]}`;
const shortDate = (date: string) => new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
const same = (a: number[], b: number[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** "Every day", "Every other week on Mon, Thu", "Monthly on the 15th · until Dec 1". */
export function repeatLabel(r: Repeat, today?: string): string {
  let text: string;
  if (r.month_days.length) {
    text = `Monthly on the ${r.month_days.map((d) => (d === LAST_DAY ? "last day" : ordinal(d))).join(", ")}`;
  } else {
    const days = same(r.weekdays, [0, 1, 2, 3, 4]) ? "weekdays" : same(r.weekdays, [5, 6]) ? "weekends" : r.weekdays.map((d) => WEEKDAYS[d]).join(", ");
    const everyDay = r.weekdays.length === 0 || r.weekdays.length === 7;
    if (r.every_weeks <= 1) text = everyDay ? "Every day" : days === "weekdays" || days === "weekends" ? `On ${days}` : `Every ${days}`;
    else text = `Every ${r.every_weeks === 2 ? "other week" : `${r.every_weeks} weeks`}${everyDay ? ", all week" : ` on ${days}`}`;
  }
  if (r.start_date && (!today || r.start_date > today)) text += ` · from ${shortDate(r.start_date)}`;
  if (r.end_date) text += ` · until ${shortDate(r.end_date)}`;
  return text;
}

type Mode = "once" | "daily" | "weekly" | "monthly";
const MODES: { id: Mode; label: string }[] = [
  { id: "once", label: "This day" },
  { id: "daily", label: "Every day" },
  { id: "weekly", label: "Weekly" },
  { id: "monthly", label: "Monthly" },
];

const modeOf = (r: Repeat): Mode => (r.month_days.length ? "monthly" : r.weekdays.length || r.every_weeks > 1 ? "weekly" : "daily");

interface Props {
  /** Null = a task for one day only. */
  value: Repeat | null;
  onChange: (r: Repeat | null) => void;
  /** The day being looked at: what "this day" means and the default weekday / day of month. */
  date: string;
  /** False when editing a task that already repeats: it cannot become a one-day task. */
  allowOnce: boolean;
  /** A store's task is for "this day"; a project's is open until it is done. */
  onceLabel?: string;
  onceHint?: string;
  /** Said after the schedule, e.g. how the next one appears. */
  repeatHint?: string;
}

/** How often a task comes back, as in ClickUp's recurring settings. */
export function RepeatFields({ value, onChange, date, allowOnce, onceLabel = "This day", onceHint = "On this day's list only.", repeatHint }: Props) {
  const [mode, setMode] = useState<Mode>(value ? modeOf(value) : "once");
  const r = value ?? EVERY_DAY;
  const dates = { start_date: r.start_date, end_date: r.end_date };

  const pick = (m: Mode) => {
    setMode(m);
    if (m === "once") onChange(null);
    if (m === "daily") onChange({ ...EVERY_DAY, ...dates });
    if (m === "weekly") onChange({ ...EVERY_DAY, ...dates, weekdays: r.weekdays.length ? r.weekdays : [weekdayOf(date)], every_weeks: r.every_weeks });
    if (m === "monthly") onChange({ ...EVERY_DAY, ...dates, month_days: r.month_days.length ? r.month_days : [Number(date.slice(8))] });
  };
  const toggleDay = (d: number) => {
    const next = r.weekdays.includes(d) ? r.weekdays.filter((x) => x !== d) : [...r.weekdays, d].sort();
    if (next.length) onChange({ ...r, weekdays: next });
  };

  return (
    <fieldset className="space-y-3">
      <legend className="mb-1.5 text-sm font-medium text-muted-foreground">Repeats</legend>
      <div className="grid auto-cols-fr grid-flow-col gap-1 rounded-lg bg-muted p-1">
        {MODES.filter((m) => allowOnce || m.id !== "once").map((m) => (
          <button
            key={m.id}
            type="button"
            aria-pressed={mode === m.id}
            onClick={() => pick(m.id)}
            className={cx("min-h-10 rounded-md px-1 text-sm font-medium transition", mode === m.id ? "bg-card text-foreground shadow-card" : "text-muted-foreground hover:text-foreground")}
          >
            {m.id === "once" ? onceLabel : m.label}
          </button>
        ))}
      </div>

      {mode === "weekly" && (
        <div className="fade space-y-3">
          <div className="grid grid-cols-7 gap-1.5">
            {WEEKDAYS.map((d, i) => (
              <button
                key={d}
                type="button"
                aria-pressed={r.weekdays.includes(i)}
                onClick={() => toggleDay(i)}
                className={cx(
                  "min-h-11 rounded-lg border text-sm font-medium transition active:scale-95",
                  r.weekdays.includes(i) ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card hover:border-ring",
                )}
              >
                {d}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-[15px]">
            Every
            <select aria-label="Every how many weeks" value={r.every_weeks} onChange={(e) => onChange({ ...r, every_weeks: Number(e.target.value) })} className={cx(inputClass, "w-auto")}>
              {Array.from({ length: Math.max(MAX_EVERY_WEEKS, r.every_weeks) }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  {n === 1 ? "week" : n === 2 ? "other week" : `${n} weeks`}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {mode === "monthly" && (
        <label className="fade flex items-center gap-2 text-[15px]">
          On the
          <select aria-label="Day of the month" value={r.month_days[0] ?? 1} onChange={(e) => onChange({ ...r, month_days: [Number(e.target.value)] })} className={cx(inputClass, "w-auto")}>
            {Array.from({ length: 31 }, (_, i) => i + 1).map((n) => (
              <option key={n} value={n}>
                {ordinal(n)}
              </option>
            ))}
            <option value={LAST_DAY}>last day</option>
          </select>
          of each month
        </label>
      )}

      {mode !== "once" && (
        <div className="fade grid grid-cols-2 gap-3">
          <Field label="Starts (optional)">
            <Input type="date" value={r.start_date ?? ""} onChange={(e) => onChange({ ...r, start_date: e.target.value || null })} />
          </Field>
          <Field label="Ends (optional)">
            <Input type="date" value={r.end_date ?? ""} min={r.start_date ?? undefined} onChange={(e) => onChange({ ...r, end_date: e.target.value || null })} />
          </Field>
        </div>
      )}
      <p className="text-sm text-muted-foreground">{value ? [`${repeatLabel(value)}.`, repeatHint].filter(Boolean).join(" ") : onceHint}</p>
    </fieldset>
  );
}
