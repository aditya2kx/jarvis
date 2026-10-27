/** Display strings for ADP unavailability entries, matching how ADP words its cards. */

import { minToTime } from "@/lib/labor/schedule-inputs";
import type { PatternLine } from "@/lib/labor/unavailability";

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type Hours = { all_day: boolean; from_time: string | null; to_time: string | null };
type Entry = Hours & { first_date: string; repeat_weekday: number | null; repeat_until: string | null };

function localDate(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y!, m! - 1, d!);
}

/** "Sat, Oct 31" */
export function dayLabel(iso: string): string {
  return localDate(iso).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

/** "Oct 31" */
export function shortDate(iso: string): string {
  return localDate(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** 390 → "6:30 AM" */
export function clock(min: number): string {
  const [h, m] = minToTime(min % (24 * 60)).split(":").map(Number);
  return `${h! % 12 || 12}:${String(m).padStart(2, "0")} ${h! < 12 ? "AM" : "PM"}`;
}

/** 390 → "6:30a", 600 → "10a" — for dense grid cells. */
export function clockShort(min: number): string {
  const [h, m] = minToTime(min % (24 * 60)).split(":").map(Number);
  return `${h! % 12 || 12}${m ? `:${String(m).padStart(2, "0")}` : ""}${h! < 12 ? "a" : "p"}`;
}

export function hhmm(t: string): number {
  return Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
}

export function hoursText(r: Hours): string {
  return r.all_day || !r.from_time || !r.to_time ? "All day" : `${clock(hhmm(r.from_time))}–${clock(hhmm(r.to_time))}`;
}

/** [0,1,2,3,4,6] → "Mon–Fri, Sun". */
export function weekdaysText(days: number[]): string {
  const parts: string[] = [];
  for (let i = 0; i < days.length; ) {
    let j = i;
    while (j + 1 < days.length && days[j + 1] === days[j]! + 1) j++;
    parts.push(j - i >= 2 ? `${WEEKDAYS[days[i]!]}–${WEEKDAYS[days[j]!]}` : days.slice(i, j + 1).map((d) => WEEKDAYS[d]).join(", "));
    i = j + 1;
  }
  return parts.join(", ");
}

/** "Every Sat · 6:00 AM–10:00 AM · Oct 31 – Nov 28" / "Sun, Oct 4 · All day" — ADP's card, one line. */
export function whenText(r: Entry): string {
  if (r.repeat_weekday != null) {
    return `Every ${WEEKDAYS[r.repeat_weekday]} · ${hoursText(r)} · ${shortDate(r.first_date)}${
      r.repeat_until ? ` – ${shortDate(r.repeat_until)}` : " onward"
    }`;
  }
  return `${dayLabel(r.first_date)} · ${hoursText(r)}`;
}

/** "Mon–Fri · All day · until Oct 30"; patterns still present in the last week ADP was read are "ongoing". */
export function lineText<T>(l: PatternLine<T>, lastWeekFrom: string, nextWeek: string): string {
  if (!l.weekdays.length) return `${dayLabel(l.date)} · ${hoursText(l)}`;
  const start = l.date >= nextWeek ? ` · from ${shortDate(l.date)}` : "";
  const end = !l.until ? "" : l.until >= lastWeekFrom ? " · ongoing" : ` · until ${shortDate(l.until)}`;
  return `${weekdaysText(l.weekdays)} · ${hoursText(l)}${start}${end}`;
}

/** "Garcia, Jacob" → "JG" */
export function initials(name: string): string {
  const [last = "", first = ""] = name.split(",").map((s) => s.trim());
  return `${first[0] ?? ""}${last[0] ?? ""}`.toUpperCase() || "?";
}

/** "Garcia, Jacob" → "Jacob G." — grid rows are narrow. */
export function shortName(name: string): string {
  const [last = "", first = ""] = name.split(",").map((s) => s.trim());
  return first ? `${first.split(" ")[0]} ${last[0] ?? ""}.` : last;
}

export function expiryText(r: { hours_left: number | null; expires_at_ct: string | null }): { text: string; urgent: boolean } | null {
  if (r.hours_left == null || !r.expires_at_ct) return null;
  const h = r.hours_left;
  return { text: h < 48 ? `expires in ${Math.max(1, Math.round(h))}h` : `expires ${shortDate(r.expires_at_ct)}`, urgent: h < 48 };
}

/** BQ TIMESTAMP ("2026-09-27 18:35:40.304467+00") → "Sep 27, 1:35 PM CT". */
export function centralTime(iso: string | null | undefined): string {
  if (!iso) return "not read yet";
  const d = new Date(iso.replace(" ", "T").replace(/\+00$/, "Z"));
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-US", { timeZone: "America/Chicago", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " CT";
}
