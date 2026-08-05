/**
 * Compare order-reco materialization timestamps after an async refresh enqueue.
 * Used by usage-day Apply poll (Issue #218 UX) — same idea as ADP schedule scraped_at poll.
 */

export function orderRecoRefreshedAdvanced(
  baseline: string | null | undefined,
  latest: string | null | undefined,
): boolean {
  if (!latest) return false;
  if (!baseline) return true;
  const b = Date.parse(baseline);
  const l = Date.parse(latest);
  if (Number.isNaN(l)) return false;
  if (Number.isNaN(b)) return true;
  return l > b;
}
