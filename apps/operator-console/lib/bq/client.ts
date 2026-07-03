import "server-only";
import { BigQuery, BigQueryDate, BigQueryDatetime, BigQueryInt, BigQueryTime, BigQueryTimestamp } from "@google-cloud/bigquery";

const BQ_WRAPPER_CLASSES = [BigQueryDate, BigQueryDatetime, BigQueryInt, BigQueryTime, BigQueryTimestamp];

// Reads the BHAGA warehouse the console is a client of — same project/dataset
// as cloud/webhook/handler.py and core/order_reco.py. See
// docs/operator-console/ARCHITECTURE.md §1 (no metric math here, only
// `SELECT * FROM vw_*` reads and the same MERGE writes the Slack command uses).
export const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
export const DATASET = process.env.BQ_DATASET ?? "bhaga";

let _bq: BigQuery | null = null;

function bq(): BigQuery {
  if (!_bq) _bq = new BigQuery({ projectId: PROJECT });
  return _bq;
}

/** Fully-qualified, backtick-quoted `project.dataset.name` for a BQ object. */
export function fq(name: string): string {
  return `\`${PROJECT}.${DATASET}.${name}\``;
}

// The BQ client returns DATE/TIME/DATETIME/TIMESTAMP/INT64 columns as
// BigQueryDate/BigQueryTime/BigQueryDatetime/BigQueryTimestamp/BigQueryInt
// class instances (each wrapping `{ value: "<string>" }`). Class instances
// can't cross the Server->Client Component boundary as RSC props, so every
// row read through `q()` is deep-sanitized to plain JSON-safe values here —
// once, centrally — rather than leaving each of the ~10 page/query call
// sites to remember it.
//
// Must discriminate via `instanceof`, not "has a .value property" — a row
// with a column literally named `value` (e.g. store_config.value) is a
// plain object that happens to have a `.value` key, not a BQ wrapper, and
// duck-typing on that shape silently corrupts it into a bare string.
function sanitize(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(sanitize);
  if (BQ_WRAPPER_CLASSES.some((cls) => value instanceof cls)) {
    return (value as { value: string | number }).value;
  }
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, val]) => [k, sanitize(val)]),
    );
  }
  return value;
}

/**
 * Run a parameterized query against BigQuery and return typed, plain-object
 * rows (safe to pass as Server->Client Component props or serialize).
 * Every caller in lib/bq/queries.ts and lib/bq/writes.ts goes through this —
 * never build ad-hoc BigQuery() instances elsewhere.
 */
export async function q<T = Record<string, unknown>>(
  sql: string,
  params?: Record<string, unknown>,
): Promise<T[]> {
  const [rows] = await bq().query({ query: sql, params, location: "US" });
  return rows.map((r) => sanitize(r)) as T[];
}

// A DATE-typed param — pass through BigQuery.date() so BQ infers DATE, not
// STRING, when a param is compared against a DATE column (e.g. the MERGE
// USING clauses in lib/bq/writes.ts). Plain strings would bind as STRING and
// silently fail every DATE-column comparison.
export function dateParam(value: string) {
  return BigQuery.date(value);
}

// The Node client infers plain JS numbers as FLOAT64 params. Several TVFs
// (e.g. tvf_order_reco_slot1/2) declare an INT64 argument and reject a
// FLOAT64 with "no matching signature" — force INT64 wherever a param
// binds against an INT64-typed column or TVF argument.
export function intParam(value: number) {
  return BigQuery.int(value);
}

/**
 * Run a write (MERGE/INSERT/DELETE) statement. Same param binding as `q()`
 * (dates must use `dateParam`) but returns nothing — every caller in
 * lib/bq/writes.ts goes through this, mirroring the exact statements
 * `cloud/webhook/handler.py` uses so app writes converge with the Slack path.
 */
export async function mutate(sql: string, params?: Record<string, unknown>): Promise<void> {
  await bq().query({ query: sql, params, location: "US" });
}
