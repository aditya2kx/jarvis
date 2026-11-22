import "server-only";
import { BigQuery } from "@google-cloud/bigquery";
import type { StoreId } from "@/lib/config/stores";

export const PROJECT = process.env.BQ_PROJECT ?? "jarvis-bhaga-prod";
export const DATASET = process.env.BQ_DATASET ?? "bhaga";

// One dataset per store, same schema (core/migrations applied to each). The default
// store keeps DATASET, so nothing about Austin's reads or writes changes.
const STORE_DATASETS: Record<StoreId, string> = {
  palmetto: DATASET,
  houston: process.env.BQ_DATASET_HOUSTON ?? "bhaga_houston",
};

// Console-wide tables that live only in the default dataset, whatever store is selected.
const SHARED_TABLE_PREFIXES = ["console_access_grants", "console_cache_probe"];

export function storeDataset(store: StoreId): string {
  return STORE_DATASETS[store];
}

const DATASET_REF = new RegExp(
  "`" + `${PROJECT}.${DATASET}.`.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "([A-Za-z_][A-Za-z0-9_]*)",
  "g",
);

/**
 * Point a statement built with `fq()` at `store`'s dataset. Only the backtick-quoted
 * `project.dataset.` qualifier is rewritten — never params or string literals — and
 * shared console tables stay where they are.
 */
export function forStore(sql: string, store: StoreId): string {
  const dataset = storeDataset(store);
  if (dataset === DATASET) return sql;
  return sql.replace(DATASET_REF, (ref, name: string) =>
    SHARED_TABLE_PREFIXES.some((p) => name.startsWith(p)) ? ref : "`" + `${PROJECT}.${dataset}.${name}`,
  );
}

let _bq: BigQuery | null = null;

/** The one BigQuery client — shared by lib/bq/client.ts and the query cache. */
export function bq(): BigQuery {
  if (!_bq) _bq = new BigQuery({ projectId: PROJECT });
  return _bq;
}
