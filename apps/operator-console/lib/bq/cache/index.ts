import "server-only";
import { headers } from "next/headers";
import { cache } from "react";
import { DATASET, PROJECT } from "@/lib/bq/bigquery";
import { dependencies } from "./deps";
import { bumpVersions, getEntry, putEntry, readVersions, SCHEMA_DOC, storeState } from "./firestore";
import {
  cacheKey,
  isExactJson,
  isReadOnlySql,
  namedObjects,
  paramsFingerprint,
  sameRows,
  sha,
  timeBucket,
  volatility,
} from "./policy";
import { shared } from "./shared";
import { record, snapshotStats, type Outcome } from "./stats";

/**
 * Read-through cache for every `q()` read (docs/operator-console/ARCHITECTURE.md §15),
 * backed by one shared Firestore layer.
 *
 * Correctness rules, in order:
 * - Only page renders use it. Server actions (status polls, writes) always read live.
 * - A key covers the query text, params, the shared version of every table and view the
 *   query reads (bumped from BigQuery's audit log for every writer, and by this app right
 *   after its own writes), the schema version and the UTC hour.
 * - Only results that survive a JSON round trip unchanged are cached.
 * Anything it cannot prove safe — including the store being unreachable — reads BigQuery.
 */
const pendingMisses = shared("pending", () => new Map<string, Promise<unknown[]>>());

/** One version read per page render, so every panel on a page uses the same snapshot. */
const requestVersions = cache(() => readVersions());

type Mode = "cache" | "off" | "verify" | "bypass";

// On by default once the audit-log feed is live (provisioned 2026-10-21). Kill switch:
// CONSOLE_QUERY_CACHE=off. Previews still set the env explicitly from whether the feed
// subscription exists (operator-console-preview.yml).
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
  if (h.has("next-action")) return { mode: "bypass", reason: "server action" };
  const v = h.get("x-console-cache");
  if (v === "off") return { mode: "off" };
  if (v === "verify") return { mode: "verify" };
  return { mode: "cache" };
}

/** After a successful console write: move the written tables' versions before returning. */
export async function noteWrite(sql: string): Promise<void> {
  await bumpVersions(namedObjects(sql, PROJECT, DATASET));
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
    const rows = await direct("write");
    await noteWrite(sql);
    return rows;
  }
  if (cacheDisabled()) return direct("off", "CONSOLE_QUERY_CACHE=off");
  const { mode, reason } = await requestMode();
  if (mode === "bypass") return direct("bypass", reason);
  if (mode === "off") return direct("off", "x-console-cache: off");
  const vol = volatility(sql);
  if (vol === "never") return direct("uncacheable", "non-deterministic SQL");

  const versions = await requestVersions();
  if (!versions) return direct("error", "cache store unavailable");
  const schemaVersion = versions.get(SCHEMA_DOC) ?? 0;

  const { known, deps: depsP } = dependencies(sql, params, schemaVersion);
  // First sight of this query text: run it alongside the dry run rather than after it.
  const firstRun = known ? null : live();
  firstRun?.catch(() => {});
  const deps = await depsP;
  if (!deps.ok) {
    const rows = await (firstRun ?? live());
    record({ outcome: "uncacheable", ms: elapsed(), sqlId, rows: rows.length, tables: [], reason: deps.reason });
    return rows;
  }
  const tables = deps.objects;
  const keyVersions = Object.fromEntries(tables.map((t) => [t, versions.get(t) ?? 0]));
  const key = cacheKey({
    sql,
    params: paramsFingerprint(params),
    versions: keyVersions,
    schemaVersion,
    bucket: timeBucket(new Date(), vol),
  });

  const store = async (rows: T[]): Promise<void> => {
    if (isExactJson(rows)) await putEntry(key, JSON.stringify(rows));
  };

  if (mode === "verify") {
    const [cached, rows] = await Promise.all([getEntry(key), firstRun ?? live()]);
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
    await store(rows);
    return rows;
  }

  if (!firstRun) {
    const hit = await getEntry(key);
    if (hit !== null) {
      const rows = JSON.parse(hit) as T[];
      record({ outcome: "hit", ms: elapsed(), sqlId, rows: rows.length, tables });
      return rows;
    }
  }

  // Concurrent misses on one key (two panels, two tabs) share one BigQuery job.
  let pending = pendingMisses.get(key) as Promise<T[]> | undefined;
  if (!pending) {
    pending = (firstRun ?? live()).then(async (rows) => {
      await store(rows);
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
  return { disabled: cacheDisabled(), store: storeState(), ...snapshotStats() };
}
