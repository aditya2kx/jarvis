import "server-only";
import { BigQuery } from "@google-cloud/bigquery";

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

// The BQ client returns DATE/TIME/DATETIME/TIMESTAMP columns as
// BigQueryDate/BigQueryTime/BigQueryDatetime/BigQueryTimestamp class
// instances — always `{ value: "<string>" }` at runtime, never a plain
// object. Class instances (and BigQueryInt/Big.js numerics, same shape)
// can't cross the Server->Client Component boundary as RSC props, so every
// row read through `q()` is deep-sanitized to plain JSON-safe values here —
// once, centrally — rather than leaving each of the ~10 page/query call
// sites to remember it.
function sanitize(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (Array.isArray(value)) return value.map(sanitize);
  if (typeof value === "object") {
    const v = value as { value?: unknown };
    if (typeof v.value === "string" || typeof v.value === "number") {
      return v.value; // BigQueryDate / BigQueryTime / BigQueryDatetime / BigQueryTimestamp / BigQueryInt
    }
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
