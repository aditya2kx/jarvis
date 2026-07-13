import { cookies } from "next/headers";
import { PERIOD_COOKIE, resolveRange, type DateWindow } from "@/lib/filters/range";

/**
 * Resolve Period for a page: URL `?range=` wins, else the shared `oc_range`
 * cookie, else this_month (Home default — keeps nav pages in lockstep).
 */
export async function resolvePageRange(
  rangeParam: string | string[] | undefined,
  from?: string | string[],
  to?: string | string[],
): Promise<DateWindow> {
  const jar = await cookies();
  const cookie = jar.get(PERIOD_COOKIE)?.value;
  // Ignore stale "custom" cookie without from/to — fall through to this_month.
  const fromCookie = cookie && cookie !== "custom" ? cookie : undefined;
  const raw = rangeParam ?? fromCookie;
  return resolveRange(raw, "this_month", from, to);
}
