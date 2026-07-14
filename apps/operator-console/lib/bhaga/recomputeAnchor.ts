/**
 * Pick a single REFRESH_DATE for a batch of tip-exemption edits.
 *
 * ``materialize_model_bq`` rebuilds tip-allocation tables for the whole model,
 * not one calendar day. Firing one Cloud Run job per touched date races
 * concurrent MERGEs and trips tip-pool conservation. One FORCE_MODEL job is enough.
 */
export function pickRecomputeAnchorDate(dates: readonly string[]): string | null {
  const unique = [...new Set(dates.filter(Boolean))].sort();
  return unique.length ? unique[unique.length - 1]! : null;
}
