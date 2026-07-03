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

/**
 * Run a parameterized query against BigQuery and return typed rows.
 * Every caller in lib/bq/queries.ts and lib/bq/writes.ts goes through this —
 * never build ad-hoc BigQuery() instances elsewhere.
 */
export async function q<T = Record<string, unknown>>(
  sql: string,
  params?: Record<string, unknown>,
): Promise<T[]> {
  const [rows] = await bq().query({ query: sql, params, location: "US" });
  return rows as T[];
}
