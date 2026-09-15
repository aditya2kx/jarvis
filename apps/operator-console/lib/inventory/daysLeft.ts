import type { Thresholds } from "@/components/tables/DataTable";

/**
 * Burn-down days left: how long a base lasts at current consumption with no
 * restocking (`Current Qty / Avg per day`).
 *
 * Distinct from `Days Left After Restock`, which the reco table shows per
 * delivery slot — that one assumes the order arrives. This is the "what if
 * nothing shows up" number, and it is the one that says whether a date needs
 * to move.
 */
export const DAYS_LEFT_THRESHOLDS: Thresholds = {
  warn: 7,
  bad: 4,
  direction: "lower-bad",
};

/** At or below this many burn-down days a base counts as at risk. */
export const RISKY_DAYS_LEFT = DAYS_LEFT_THRESHOLDS.bad;
