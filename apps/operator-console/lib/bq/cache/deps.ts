import "server-only";
import { bq, DATASET, PROJECT } from "@/lib/bq/bigquery";
import { namedObjects, sha } from "./policy";
import { shared } from "./shared";

/** BigQuery stops listing referenced tables past this many; treat such a query as uncacheable. */
const REFERENCED_TABLES_CAP = 50;

export type Deps = { ok: true; objects: string[] } | { ok: false; reason: string };

const memo = shared("deps", () => new Map<string, { schemaVersion: number; p: Promise<Deps> }>());

type DryRunStats = {
  statementType?: string;
  referencedTables?: { projectId?: string; datasetId?: string; tableId?: string }[];
};

async function dryRun(sql: string, params: Record<string, unknown> | undefined): Promise<Deps> {
  try {
    const [job] = await bq().createQueryJob({ query: sql, params, location: "US", dryRun: true });
    const st = (job.metadata?.statistics?.query ?? {}) as DryRunStats;
    if (st.statementType && st.statementType !== "SELECT") {
      return { ok: false, reason: `statement ${st.statementType}` };
    }
    const refs = st.referencedTables ?? [];
    if (refs.length >= REFERENCED_TABLES_CAP) return { ok: false, reason: "too many tables" };
    const objects = new Set(namedObjects(sql, PROJECT, DATASET));
    for (const t of refs) {
      if (t.projectId !== PROJECT || t.datasetId !== DATASET || !t.tableId) {
        return { ok: false, reason: `reads ${t.projectId}.${t.datasetId}` };
      }
      objects.add(t.tableId);
    }
    return { ok: true, objects: [...objects].sort() };
  } catch (e) {
    return { ok: false, reason: `dry run failed: ${String(e).slice(0, 160)}` };
  }
}

/**
 * Every table and view a SELECT reads, from a (free) BigQuery dry run — BigQuery expands
 * views and table functions itself, so nothing here parses view SQL. Memoised per query
 * text until any view or routine is redefined (`__schema__` version).
 */
export function dependencies(
  sql: string,
  params: Record<string, unknown> | undefined,
  schemaVersion: number,
): { known: boolean; deps: Promise<Deps> } {
  const id = sha(sql);
  const hit = memo.get(id);
  if (hit && hit.schemaVersion === schemaVersion) return { known: true, deps: hit.p };
  const p = dryRun(sql, params);
  memo.set(id, { schemaVersion, p });
  p.then((d) => {
    // A transient failure must not pin the query as uncacheable.
    if (!d.ok && d.reason.startsWith("dry run failed") && memo.get(id)?.p === p) memo.delete(id);
  });
  return { known: false, deps: p };
}
