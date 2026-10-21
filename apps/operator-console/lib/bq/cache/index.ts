import "server-only";
import { headers } from "next/headers";
import { catalogSnapshot, catalogState, markDirty } from "./catalog";
import { dependencies } from "./deps";
import { l2Get, l2Put, l2State } from "./firestore";
import { MemoryLru } from "./memory";
import {
  cacheKey,
  isExactJson,
  isReadOnlySql,
  paramsFingerprint,
  sameRows,
  sha,
  timeBucket,
  volatility,
} from "./policy";
import { record, snapshotStats, type Outcome } from "./stats";

export { markDirty };

/**
 * Read-through cache for every `q()` read (docs/operator-console/ARCHITECTURE.md §15).
 *
 * Correctness rules, in order:
 * - Only page renders use it. Server actions (status polls, writes) always read live, and
 *   any action marks the catalog dirty so the page re-rendered after it sees fresh versions.
 * - A key covers the query text, params, the BigQuery last-modified time of every table and
 *   view the query reads, and the UTC hour. Any write to any of those tables, by anyone,
 *   changes the key — within MAX_STALE_MS for writers outside this instance, immediately
 *   for this instance's own writes.
 * - Only results that survive a JSON round trip unchanged are cached.
 * Anything it cannot prove safe reads BigQuery directly.
 */
const MAX_CHARS = Number(process.env.CONSOLE_QUERY_CACHE_MAX_CHARS ?? 48_000_000);
const L1_TTL_MS = 6 * 3600_000;
const BUILD = process.env.K_REVISION ?? process.env.NEXT_DEPLOYMENT_ID ?? "dev";

const l1 = new MemoryLru(MAX_CHARS, L1_TTL_MS);
const pendingMisses = new Map<string, Promise<unknown[]>>();

type Mode = "cache" | "off" | "verify" | "bypass";

function cacheDisabled(): boolean {
  return process.env.CONSOLE_QUERY_CACHE === "off";
}

async function requestMode(): Promise<{ mode: Mode; reason?: string }> {
  let h: Headers;
  try {
    h = await headers();
  } catch {
    return { mode: "bypass", reason: "outside a request" };
  }
  if (h.has("next-action")) {
    markDirty();
    return { mode: "bypass", reason: "server action" };
  }
  const v = h.get("x-console-cache");
  if (v === "off") return { mode: "off" };
  if (v === "verify") return { mode: "verify" };
  return { mode: "cache" };
}

export async function readThrough<T>(
  sql: string,
  params: Record<string, unknown> | undefined,
  live: () => Promise<T[]>,
): Promise<T[]> {
  const sqlId = sha(sql).slice(0, 10);
  const t0 = performance.now();
  const elapsed = () => Math.round(performance.now() - t0);
  const direct = async (outcome: Outcome, reason?: string) => {
    const rows = await live();
    record({ outcome, ms: elapsed(), sqlId, rows: rows.length, tables: [], reason });
    return rows;
  };

  if (!isReadOnlySql(sql)) {
    try {
      return await direct("write");
    } finally {
      markDirty();
    }
  }
  if (cacheDisabled()) return direct("off", "CONSOLE_QUERY_CACHE=off");
  const { mode, reason } = await requestMode();
  if (mode === "bypass") return direct("bypass", reason);
  if (mode === "off") return direct("off", "x-console-cache: off");
  const vol = volatility(sql);
  if (vol === "never") return direct("uncacheable", "non-deterministic SQL");

  let snap;
  try {
    snap = await catalogSnapshot();
  } catch (e) {
    return direct("error", `catalog unavailable: ${String(e).slice(0, 160)}`);
  }
  const { known, deps: depsP } = dependencies(sql, params, snap.viewsEpoch);
  // First sight of this query text: run it alongside the dry run rather than after it.
  const firstRun = known ? null : live();
  firstRun?.catch(() => {});
  const deps = await depsP;
  if (!deps.ok) {
    const rows = await (firstRun ?? live());
    record({ outcome: "uncacheable", ms: elapsed(), sqlId, rows: rows.length, tables: [], reason: deps.reason });
    return rows;
  }
  const versions: Record<string, number> = {};
  for (const name of deps.objects) {
    const v = snap.versions.get(name);
    if (v === undefined) {
      const rows = await (firstRun ?? live());
      record({ outcome: "uncacheable", ms: elapsed(), sqlId, rows: rows.length, tables: deps.objects, reason: `unknown object ${name}` });
      return rows;
    }
    versions[name] = v;
  }
  const key = cacheKey({
    sql,
    params: paramsFingerprint(params),
    versions,
    viewsEpoch: snap.viewsEpoch,
    bucket: timeBucket(new Date(), vol),
    build: BUILD,
  });
  const tables = deps.objects;

  const store = (rows: T[]): void => {
    if (!isExactJson(rows)) return;
    const json = JSON.stringify(rows);
    l1.set(key, json);
    void l2Put(key, json);
  };

  if (mode === "verify") {
    const cached = l1.get(key) ?? (await l2Get(key));
    const rows = await (firstRun ?? live());
    if (cached === null) {
      record({ outcome: "verify-cold", ms: elapsed(), sqlId, rows: rows.length, tables });
    } else {
      const cmp = sameRows(JSON.parse(cached) as unknown[], JSON.parse(JSON.stringify(rows)) as unknown[]);
      record({
        outcome: cmp.equal ? "verify-match" : "verify-diff",
        ms: elapsed(),
        sqlId,
        rows: rows.length,
        tables,
        reason: cmp.equal ? (cmp.sameOrder ? undefined : "same rows, different order") : "cached != live",
      });
    }
    store(rows);
    return rows;
  }

  if (!firstRun) {
    const hit = l1.get(key);
    if (hit !== null) {
      const rows = JSON.parse(hit) as T[];
      record({ outcome: "hit", ms: elapsed(), sqlId, rows: rows.length, tables });
      return rows;
    }
    const shared = await l2Get(key);
    if (shared !== null) {
      l1.set(key, shared);
      const rows = JSON.parse(shared) as T[];
      record({ outcome: "l2-hit", ms: elapsed(), sqlId, rows: rows.length, tables });
      return rows;
    }
  }

  // Concurrent misses on one key (e.g. two panels, or two tabs) share one BigQuery job.
  let pending = pendingMisses.get(key) as Promise<T[]> | undefined;
  if (!pending) {
    pending = (firstRun ?? live()).then((rows) => {
      store(rows);
      return rows;
    });
    pendingMisses.set(key, pending);
    pending.finally(() => pendingMisses.delete(key)).catch(() => {});
  }
  const rows = await pending;
  record({ outcome: "miss", ms: elapsed(), sqlId, rows: rows.length, tables });
  // Callers may mutate what they get back; a shared miss must not hand out one array twice.
  return isExactJson(rows) ? (JSON.parse(JSON.stringify(rows)) as T[]) : rows;
}

export function cacheStats() {
  return {
    build: BUILD,
    disabled: cacheDisabled(),
    catalog: catalogState(),
    l1: { ...l1.size(), maxChars: MAX_CHARS },
    l2: l2State(),
    ...snapshotStats(),
  };
}
