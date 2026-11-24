import type { OpenHours, StoreHours } from "@/lib/labor/schedule-inputs";

const SQUARE_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

type SquarePeriod = { day_of_week?: string; start_local_time?: string; end_local_time?: string };

const minute = (hhmmss: string) => {
  const [h, m] = hhmmss.split(":").map(Number);
  return h! * 60 + (m ?? 0);
};

/**
 * Opening hours per weekday (Mon = 0) from a Square location's `business_hours` — the same
 * reading as skills/square_api/location_hours.py: split hours span the break, a midnight
 * close is 24:00, a day with no period is closed.
 */
export function squareWeekHours(location: { business_hours?: { periods?: SquarePeriod[] } }): OpenHours[] {
  const days: OpenHours[] = Array(7).fill(null);
  for (const p of location.business_hours?.periods ?? []) {
    const d = SQUARE_DAYS.indexOf(p.day_of_week ?? "");
    if (d < 0 || !p.start_local_time || !p.end_local_time) continue;
    const openMin = minute(p.start_local_time);
    let closeMin = minute(p.end_local_time);
    if (closeMin <= openMin) closeMin = 24 * 60;
    const cur = days[d];
    days[d] = cur
      ? { openMin: Math.min(cur.openMin, openMin), closeMin: Math.max(cur.closeMin, closeMin) }
      : { openMin, closeMin };
  }
  return days;
}

export const sameHours = (a: OpenHours[], b: OpenHours[]) => JSON.stringify(a) === JSON.stringify(b);

export type HoursCopy = { asOf: string; days: OpenHours[] };

/** A change older than this is no longer called out. */
export const CHANGE_NOTE_DAYS = 14;

/**
 * Store hours from a live Square read (null when it failed) and the nightly copies, newest
 * first. A live read that differs from last night's copy is a change seen today.
 */
export function resolveStoreHours(
  live: { days: OpenHours[]; readAt: string } | null,
  copies: HoursCopy[],
  todayIso: string,
): StoreHours | null {
  const latest = copies[0];
  if (!live && !latest) return null;
  let changed: StoreHours["changed"] = null;
  if (live && latest && !sameHours(live.days, latest.days)) {
    changed = { on: live.readAt.slice(0, 10), before: latest.days };
  } else {
    for (let i = 0; i + 1 < copies.length; i++) {
      if (!sameHours(copies[i]!.days, copies[i + 1]!.days)) {
        changed = { on: copies[i]!.asOf, before: copies[i + 1]!.days };
        break;
      }
    }
  }
  const age = (iso: string) => (Date.parse(todayIso) - Date.parse(iso.slice(0, 10))) / 864e5;
  if (changed && age(changed.on) > CHANGE_NOTE_DAYS) changed = null;
  return live
    ? { days: live.days, source: "square", asOf: live.readAt, changed }
    : { days: latest!.days, source: "snapshot", asOf: latest!.asOf, changed };
}
