import "server-only";
import { bq, DATASET, PROJECT } from "@/lib/bq/bigquery";

/**
 * Version of every table and view in the dataset = its BigQuery `last_modified_time`.
 * Every writer — nightly job, Slack, console, a manual fix, a future webhook — changes
 * that timestamp when its job commits, so the cache needs no hook in any writer.
 * (No writer uses streaming inserts, whose buffer does not move it reliably.)
 */
export type CatalogSnapshot = {
  at: number;
  versions: Map<string, number>;
  /** Latest view (re)definition — part of every key, so a migration invalidates reads. */
  viewsEpoch: number;
};

/** Younger than this: use as is. */
export const FRESH_MS = 10_000;
/** Older than FRESH_MS but younger than this: use, refreshing in the background. */
export const MAX_STALE_MS = 45_000;

let snap: CatalogSnapshot | null = null;
let dirtyGen = 0;
let cleanGen = 0;
let inflight: { gen: number; p: Promise<CatalogSnapshot> } | null = null;

const VIEW_TYPE = 2;

async function load(): Promise<CatalogSnapshot> {
  const at = Date.now();
  const [rows] = await bq().query({
    query: `SELECT table_id, type, last_modified_time FROM \`${PROJECT}.${DATASET}.__TABLES__\``,
    location: "US",
  });
  const versions = new Map<string, number>();
  let viewsEpoch = 0;
  for (const r of rows as { table_id: string; type: unknown; last_modified_time: unknown }[]) {
    const lm = Number(r.last_modified_time);
    versions.set(r.table_id, lm);
    if (Number(r.type) === VIEW_TYPE && lm > viewsEpoch) viewsEpoch = lm;
  }
  return { at, versions, viewsEpoch };
}

function refresh(): Promise<CatalogSnapshot> {
  if (inflight && inflight.gen === dirtyGen) return inflight.p;
  const gen = dirtyGen;
  const p = load()
    .then((s) => {
      // A refresh that started before a write must not mark the catalog clean.
      if (!snap || s.at >= snap.at) snap = s;
      if (gen > cleanGen) cleanGen = gen;
      return s;
    })
    .finally(() => {
      if (inflight?.p === p) inflight = null;
    });
  inflight = { gen, p };
  return p;
}

/**
 * A snapshot no older than MAX_STALE_MS (the agreed staleness bound for writes from
 * outside this instance). After `markDirty()` the next caller waits for a fresh one.
 */
export async function catalogSnapshot(now = Date.now()): Promise<CatalogSnapshot> {
  const dirty = dirtyGen > cleanGen;
  const age = snap ? now - snap.at : Infinity;
  if (snap && !dirty && age < FRESH_MS) return snap;
  if (snap && !dirty && age < MAX_STALE_MS) {
    refresh().catch((e) => console.error(`console_cache_catalog_refresh_failed err=${String(e)}`));
    return snap;
  }
  return refresh();
}

/** This instance just wrote (or an action ran): the next read must see a fresh catalog. */
export function markDirty(): void {
  dirtyGen += 1;
}

export function catalogState(): { ageMs: number | null; objects: number; dirty: boolean } {
  return {
    ageMs: snap ? Date.now() - snap.at : null,
    objects: snap?.versions.size ?? 0,
    dirty: dirtyGen > cleanGen,
  };
}
