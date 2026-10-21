import "server-only";
import { promises as fs } from "fs";
import { fq, intParam, mutate, q } from "@/lib/bq/client";
import { normalizeEmail, normalizeGrant, parseGrant, type Grant, type GrantStatus, type GrantVersion } from "./policy";

// Append-only: every save is a new version per email; the newest version is live and
// older ones stay as history (who changed what, when). Preview services point
// CONSOLE_ACCESS_TABLE at their own throwaway sandbox table.
export const PROD_ACCESS_TABLE = "console_access_grants";

export function accessTable(): string {
  return process.env.CONSOLE_ACCESS_TABLE || PROD_ACCESS_TABLE;
}

/** Local dev only (`BYPASS_IAP_EMAIL` set): a JSON file instead of BigQuery. */
function devFile(): string | null {
  return process.env.BYPASS_IAP_EMAIL && process.env.CONSOLE_ACCESS_FILE ? process.env.CONSOLE_ACCESS_FILE : null;
}

type Row = {
  email: string;
  version: number | string;
  status: string;
  grant_json: string;
  note: string | null;
  created_by: string;
  created_at: string | { value: string };
};

function toVersion(r: Row): GrantVersion {
  return {
    email: normalizeEmail(r.email),
    version: Number(r.version),
    status: r.status === "revoked" ? "revoked" : "active",
    grant: parseGrant(r.grant_json),
    note: r.note,
    createdBy: r.created_by,
    createdAt: typeof r.created_at === "object" ? r.created_at.value : String(r.created_at),
  };
}

async function readDevRows(path: string): Promise<Row[]> {
  try {
    return JSON.parse(await fs.readFile(path, "utf8")) as Row[];
  } catch {
    return [];
  }
}

async function allRows(): Promise<Row[]> {
  const file = devFile();
  if (file) return readDevRows(file);
  return q<Row>(
    `SELECT email, version, status, grant_json, note, created_by, created_at
     FROM ${fq(accessTable())}
     ORDER BY created_at DESC, email, version DESC
     LIMIT 2000`,
  );
}

// Every page view of a non-owner reads the live grants; keep one BigQuery round trip per
// instance per TTL. A save on this instance clears it at once; other instances catch up
// within the TTL.
const TTL_MS = 30_000;
let cache: { at: number; rows: Promise<Row[]> } | null = null;

function cachedRows(): Promise<Row[]> {
  if (!cache || Date.now() - cache.at > TTL_MS) {
    const rows = allRows();
    cache = { at: Date.now(), rows };
    rows.catch(() => {
      if (cache?.rows === rows) cache = null;
    });
  }
  return cache.rows;
}

function invalidateGrantCache(): void {
  cache = null;
}

function newestFirst(a: GrantVersion, b: GrantVersion): number {
  return b.createdAt.localeCompare(a.createdAt) || b.version - a.version;
}

/** Live (newest) version per email. Throws when the store can't be read — callers fail closed. */
export async function latestGrants(): Promise<Map<string, GrantVersion>> {
  const out = new Map<string, GrantVersion>();
  for (const v of (await cachedRows()).map(toVersion)) {
    const cur = out.get(v.email);
    if (!cur || v.version > cur.version) out.set(v.email, v);
  }
  return out;
}

/** Every saved version, newest first (optionally for one email). */
export async function grantHistory(email?: string, limit = 100): Promise<GrantVersion[]> {
  const e = email ? normalizeEmail(email) : null;
  return (await allRows())
    .map(toVersion)
    .filter((v) => !e || v.email === e)
    .sort(newestFirst)
    .slice(0, limit);
}

export async function appendGrant({
  email,
  status,
  grant,
  note,
  by,
}: {
  email: string;
  status: GrantStatus;
  grant: Grant;
  note: string | null;
  by: string;
}): Promise<void> {
  const e = normalizeEmail(email);
  const grantJson = JSON.stringify(normalizeGrant(grant));
  const file = devFile();
  if (file) {
    const rows = await readDevRows(file);
    const version = Math.max(0, ...rows.filter((r) => normalizeEmail(r.email) === e).map((r) => Number(r.version))) + 1;
    rows.push({
      email: e,
      version,
      status,
      grant_json: grantJson,
      note,
      created_by: by,
      created_at: new Date().toISOString(),
    });
    await fs.writeFile(file, JSON.stringify(rows, null, 2));
  } else {
    await mutate(
      `INSERT INTO ${fq(accessTable())} (email, version, status, grant_json, note, created_by, created_at)
       SELECT @email, COALESCE(MAX(version), 0) + @one, @status, @grant_json, @note, @by, CURRENT_TIMESTAMP()
       FROM ${fq(accessTable())}
       WHERE email = @email`,
      { email: e, one: intParam(1), status, grant_json: grantJson, note, by },
      { email: "STRING", one: "INT64", status: "STRING", grant_json: "STRING", note: "STRING", by: "STRING" },
    );
  }
  invalidateGrantCache();
}
