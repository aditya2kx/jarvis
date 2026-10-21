import "server-only";
import { BigQuery } from "@google-cloud/bigquery";

export const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
export const DATASET = process.env.BQ_DATASET ?? "bhaga";

let _bq: BigQuery | null = null;

/** The one BigQuery client — shared by lib/bq/client.ts and the query cache. */
export function bq(): BigQuery {
  if (!_bq) _bq = new BigQuery({ projectId: PROJECT });
  return _bq;
}
