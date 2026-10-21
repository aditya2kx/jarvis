/** Per-read outcomes, kept in memory for `/api/cache-stats` (no row data — ids, timings, table names). */
export type Outcome =
  | "hit"
  | "l2-hit"
  | "miss"
  | "off"
  | "bypass"
  | "uncacheable"
  | "write"
  | "error"
  | "verify-match"
  | "verify-diff"
  | "verify-cold";

export type ReadEvent = {
  at: string;
  outcome: Outcome;
  ms: number;
  sqlId: string;
  rows: number | null;
  tables: string[];
  reason?: string;
};

const RING = 500;
const events: ReadEvent[] = [];
const counts: Partial<Record<Outcome, number>> = {};

export function record(e: Omit<ReadEvent, "at">): void {
  counts[e.outcome] = (counts[e.outcome] ?? 0) + 1;
  events.push({ at: new Date().toISOString(), ...e });
  if (events.length > RING) events.splice(0, events.length - RING);
  if (e.outcome === "verify-diff" || e.outcome === "error") {
    console.error(
      `console_cache_${e.outcome} sql=${e.sqlId} tables=${e.tables.join(",")} ${e.reason ?? ""}`.trim(),
    );
  }
}

export function snapshotStats(): { counts: typeof counts; recent: ReadEvent[] } {
  return { counts: { ...counts }, recent: [...events].reverse() };
}
