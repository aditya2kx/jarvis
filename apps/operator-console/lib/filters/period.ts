import { cookies } from "next/headers";
import {
  FROM_COOKIE,
  GRAIN_COOKIE,
  PERIOD_COOKIE,
  TO_COOKIE,
  parseGrain,
  resolveRange,
  type DateWindow,
  type Grain,
} from "@/lib/filters/range";

/**
 * Resolve Period for a page: URL `?range=` (+ from/to) wins, else the shared
 * `oc_range` cookie (and `oc_from`/`oc_to` when custom), else this_month
 * (Home default — keeps nav pages in lockstep).
 */
export async function resolvePageRange(
  rangeParam: string | string[] | undefined,
  from?: string | string[],
  to?: string | string[],
): Promise<DateWindow> {
  const jar = await cookies();
  const cookie = jar.get(PERIOD_COOKIE)?.value;
  const fromCookie = jar.get(FROM_COOKIE)?.value;
  const toCookie = jar.get(TO_COOKIE)?.value;

  // URL range present → URL from/to only (do not mix cookie bounds).
  if (rangeParam !== undefined && rangeParam !== null && String(rangeParam).length > 0) {
    return resolveRange(rangeParam, "this_month", from, to);
  }

  if (cookie === "custom") {
    return resolveRange("custom", "this_month", fromCookie, toCookie);
  }
  if (cookie) {
    return resolveRange(cookie, "this_month");
  }
  return resolveRange(undefined, "this_month", from, to);
}

/**
 * Resolve Aggregation grain: URL `?grain=` wins, else `oc_grain` cookie, else day.
 */
export async function resolvePageGrain(
  grainParam: string | string[] | undefined,
  fallback: Grain = "day",
): Promise<Grain> {
  if (grainParam !== undefined && grainParam !== null && String(grainParam).length > 0) {
    return parseGrain(grainParam, fallback);
  }
  const jar = await cookies();
  const cookie = jar.get(GRAIN_COOKIE)?.value;
  return parseGrain(cookie, fallback);
}
