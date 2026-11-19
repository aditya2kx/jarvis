/**
 * BigQuery aborts one of two DML statements / transactions that touch the same table
 * at once ("Could not serialize access to table … due to concurrent update"). The
 * webhook sync, a console Reapply chunk and a rule commit can all hit
 * plaid_transactions together, so Accounting writes retry that one error.
 * Mirrors skills/plaid_api/categorize.py::_with_serialize_retry.
 */
const MARKERS = ["could not serialize access", "concurrent update", "transaction is aborted"];

export const SERIALIZE_RETRY_DELAYS_MS = [1500, 4000, 8000] as const;

export function isSerializationConflict(e: unknown): boolean {
  const msg = (e instanceof Error ? e.message : String(e)).toLowerCase();
  return MARKERS.some((m) => msg.includes(m));
}

export async function withSerializeRetry<T>(
  fn: () => Promise<T>,
  opts: {
    label: string;
    delaysMs?: readonly number[];
    sleep?: (ms: number) => Promise<void>;
  },
): Promise<T> {
  const delays = opts.delaysMs ?? SERIALIZE_RETRY_DELAYS_MS;
  const sleep = opts.sleep ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn();
    } catch (e) {
      if (attempt >= delays.length || !isSerializationConflict(e)) throw e;
      console.info(
        `plaid_bq_serialize_retry label=${opts.label} attempt=${attempt + 1}/${delays.length} ` +
          `retry_in_ms=${delays[attempt]}`,
      );
      await sleep(delays[attempt]);
    }
  }
}
