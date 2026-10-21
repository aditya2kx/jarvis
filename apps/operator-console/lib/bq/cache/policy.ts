import { createHash } from "crypto";

/**
 * Pure rules for the read-through query cache (docs/operator-console/ARCHITECTURE.md §15).
 * Nothing here touches BigQuery, Firestore or the request — so every rule that decides
 * whether a cached number may be shown is unit-testable.
 */

/** Bump when the shape of what is cached changes, so old L2 entries are never read. */
export const CACHE_SCHEMA = "1";

export type Volatility = "stable" | "minute" | "never";

const LEADING_NOISE = /^(?:\s+|--[^\n]*\n|\/\*[\s\S]*?\*\/)*/;
const READ_START = /^(?:select|with|\()/i;
// Functions whose result changes without any table changing. CURRENT_DATE is not here:
// every key already carries the UTC hour (timeBucket), and a calendar date in any
// whole-hour time zone (America/Chicago included) only changes on an hour boundary.
const MINUTE_FNS = /\b(?:current_timestamp|current_datetime|current_time|now)\s*\(/i;
const NEVER_FNS = /\b(?:rand|generate_uuid|session_user)\s*\(|@@/i;

/** A statement that only reads (SELECT / WITH). Anything else is treated as a write. */
export function isReadOnlySql(sql: string): boolean {
  return READ_START.test(sql.replace(LEADING_NOISE, ""));
}

export function volatility(sql: string): Volatility {
  if (NEVER_FNS.test(sql)) return "never";
  if (MINUTE_FNS.test(sql)) return "minute";
  return "stable";
}

/** Time component of every key: the UTC hour, or the UTC minute for clock-reading SQL. */
export function timeBucket(now: Date, v: Exclude<Volatility, "never">): string {
  const iso = now.toISOString();
  return v === "minute" ? iso.slice(0, 16) : iso.slice(0, 13);
}

/** Stable text for query params, including BigQuery wrapper types (DATE vs STRING binds differ). */
export function paramsFingerprint(params: Record<string, unknown> | undefined): string {
  if (!params) return "";
  const norm = (v: unknown): unknown => {
    if (v === null || v === undefined) return v ?? null;
    if (Array.isArray(v)) return v.map(norm);
    if (typeof v === "object") {
      const ctor = (v as object).constructor?.name ?? "Object";
      if (ctor !== "Object") return { [`$${ctor}`]: (v as { value?: unknown }).value ?? String(v) };
      return Object.fromEntries(
        Object.keys(v as Record<string, unknown>)
          .sort()
          .map((k) => [k, norm((v as Record<string, unknown>)[k])]),
      );
    }
    return v;
  };
  return JSON.stringify(norm(params));
}

export function sha(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

/**
 * The cache key: query text + params + the version of every table and view the query
 * reads + the time bucket. A write to any of those tables changes its version, so the
 * old entry simply stops being addressed — nothing has to find and delete it.
 */
export function cacheKey(parts: {
  sql: string;
  params: string;
  versions: Record<string, number>;
  viewsEpoch: number;
  bucket: string;
  build: string;
}): string {
  const versions = Object.keys(parts.versions)
    .sort()
    .map((k) => `${k}@${parts.versions[k]}`)
    .join(",");
  return sha(
    [CACHE_SCHEMA, parts.build, parts.bucket, parts.viewsEpoch, versions, parts.params, parts.sql].join("\u0000"),
  );
}

/**
 * True when `JSON.parse(JSON.stringify(value))` reproduces `value` exactly — only then may
 * a result be cached, because a hit is served from that JSON. Rejects class instances
 * (e.g. a NUMERIC Big), non-finite numbers, bigint and undefined fields.
 */
export function isExactJson(value: unknown): boolean {
  if (value === null) return true;
  switch (typeof value) {
    case "string":
    case "boolean":
      return true;
    case "number":
      return Number.isFinite(value);
    case "object": {
      if (Array.isArray(value)) return value.every(isExactJson);
      const proto = Object.getPrototypeOf(value);
      if (proto !== Object.prototype && proto !== null) return false;
      return Object.values(value as Record<string, unknown>).every(isExactJson);
    }
    default:
      return false;
  }
}

/** Row-order-insensitive comparison of two result sets (for the parity check). */
export function sameRows(a: unknown[], b: unknown[]): { equal: boolean; sameOrder: boolean } {
  const ja = a.map((r) => JSON.stringify(r));
  const jb = b.map((r) => JSON.stringify(r));
  const sameOrder = ja.length === jb.length && ja.every((r, i) => r === jb[i]);
  if (sameOrder) return { equal: true, sameOrder };
  if (ja.length !== jb.length) return { equal: false, sameOrder };
  const sb = [...jb].sort();
  return { equal: [...ja].sort().every((r, i) => r === sb[i]), sameOrder };
}

/** Names of `dataset` objects a statement names directly (fq() or literal backtick refs). */
export function namedObjects(sql: string, project: string, dataset: string): string[] {
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&");
  const re = new RegExp(
    "`?(?:" + esc(project) + "`?\\.`?)?" + esc(dataset) + "`?\\.`?([A-Za-z_][A-Za-z0-9_]*)`?",
    "g",
  );
  const out = new Set<string>();
  for (const m of sql.matchAll(re)) out.add(m[1]);
  return [...out];
}
