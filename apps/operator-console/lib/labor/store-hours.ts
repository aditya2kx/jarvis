import "server-only";

import { fq, q } from "@/lib/bq/client";
import { STORE_SQUARE_LOCATION, parseStore } from "@/lib/config/stores";
import { chicagoTodayIso } from "@/lib/filters/range";
import { resolveStoreHours, squareWeekHours, type HoursCopy } from "@/lib/labor/open-hours";
import type { OpenHours, StoreHours } from "@/lib/labor/schedule-inputs";
import { getLocation } from "@/lib/recognition/square";

const LIVE_TTL_MS = 60 * 60 * 1000;
const live = new Map<string, { at: number; days: OpenHours[]; readAt: string }>();

async function liveHours(store: string): Promise<{ days: OpenHours[]; readAt: string } | null> {
  const hit = live.get(store);
  if (hit && Date.now() - hit.at < LIVE_TTL_MS) return hit;
  try {
    const days = squareWeekHours(await getLocation(STORE_SQUARE_LOCATION[parseStore(store)]));
    if (!days.some(Boolean)) throw new Error("location has no business_hours");
    const entry = { at: Date.now(), days, readAt: new Date().toISOString() };
    live.set(store, entry);
    return entry;
  } catch (e) {
    console.warn(`[store_hours] store=${store} Square read failed, using the nightly copy: ${e instanceof Error ? e.message : e}`);
    return null;
  }
}

type Row = { as_of_date: { value: string } | string; day_of_week: number; open_min: number | null; close_min: number | null };

async function copies(store: string): Promise<HoursCopy[]> {
  try {
    const rows = await q<Row>(
      `SELECT as_of_date, day_of_week, open_min, close_min
       FROM ${fq("square_location_hours")}
       WHERE store = @store AND as_of_date >= DATE_SUB(CURRENT_DATE('America/Chicago'), INTERVAL 60 DAY)
       ORDER BY as_of_date DESC, day_of_week`,
      { store },
    );
    const byDate = new Map<string, OpenHours[]>();
    for (const r of rows) {
      const asOf = typeof r.as_of_date === "string" ? r.as_of_date : r.as_of_date.value;
      const days = byDate.get(asOf) ?? byDate.set(asOf, Array(7).fill(null)).get(asOf)!;
      if (r.open_min != null && r.close_min != null) {
        days[Number(r.day_of_week)] = { openMin: Number(r.open_min), closeMin: Number(r.close_min) };
      }
    }
    return [...byDate].map(([asOf, days]) => ({ asOf, days }));
  } catch (e) {
    console.warn(`[store_hours] store=${store} no nightly copies: ${e instanceof Error ? e.message : e}`);
    return [];
  }
}

/** The store's opening hours: Square live (cached an hour), else the latest nightly copy; null when neither. */
export async function storeHours(store: string): Promise<StoreHours | null> {
  const [l, c] = await Promise.all([liveHours(store), copies(store)]);
  return resolveStoreHours(l, c, chicagoTodayIso());
}
