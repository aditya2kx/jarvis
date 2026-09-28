/**
 * Client-safe types + pure helpers for the order-reco refresh status
 * (server side: lib/bhaga/orderReco.ts). Issue #350.
 */

export type OrderRecoState = "idle" | "running" | "stale" | "failed";

export type OrderRecoStatus = {
  state: OrderRecoState;
  runId: string | null;
  startedAt: string | null;
  trigger: string | null;
  lastCommittedRunId: string | null;
  lastCommittedAt: string | null;
  lastLatencyMs: number | null;
  p95LatencyMs: number | null;
  error: string | null;
  /** Latest status of the run passed as `awaitRunId`; null until it has started. */
  awaitedStatus: "running" | "committed" | "superseded" | "failed" | null;
  /** Seconds since the last reco-input edit (last hour), from inventory_edit_log. */
  lastEditAgeS: number | null;
};

export type OrderRecoChange = {
  deliveryDate: string;
  /** TOTAL tubs for the date; `before` is null when there is no prior generation. */
  before: number | null;
  after: number;
  /** Bases whose tubs moved, largest move first. */
  items: { item: string; before: number; after: number }[];
};

/** Typical refresh time shown while running, from recent committed runs. */
export function typicalSeconds(status: Pick<OrderRecoStatus, "p95LatencyMs" | "lastLatencyMs">): number {
  const ms = status.p95LatencyMs ?? status.lastLatencyMs ?? 20_000;
  return Math.max(5, Math.round(ms / 1000));
}

/** Epoch ms for ISO-8601 or BigQuery's `YYYY-MM-DD HH:MM:SS[.ffffff]+00` TIMESTAMP string. */
export function parseTimestamp(ts: string | null): number {
  if (!ts) return NaN;
  return Date.parse(ts.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00"));
}

export function secondsSince(iso: string | null, now: number = Date.now()): number | null {
  const t = parseTimestamp(iso);
  return Number.isFinite(t) ? Math.max(0, Math.round((now - t) / 1000)) : null;
}

export function relativeAgo(iso: string | null, now: number = Date.now()): string | null {
  const s = secondsSince(iso, now);
  if (s == null) return null;
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

/**
 * True once the refresh the operator is waiting on has landed. With a run id
 * that run must itself be committed or superseded (a superseded run lost to a
 * concurrent one that already committed); without one (an action ack that
 * carried no run id) any commit newer than the baseline counts.
 */
export function awaitedRunLanded(
  status: OrderRecoStatus,
  awaitRunId: string | null,
  baselineCommittedAt: string | null,
): boolean {
  if (awaitRunId) return status.awaitedStatus === "committed" || status.awaitedStatus === "superseded";
  return status.state !== "running" && hasCommittedSince(status, baselineCommittedAt);
}

/** True once any run newer than `baselineCommittedAt` has committed. */
export function hasCommittedSince(status: OrderRecoStatus, baselineCommittedAt: string | null): boolean {
  if (!status.lastCommittedAt) return false;
  if (!baselineCommittedAt) return true;
  return parseTimestamp(status.lastCommittedAt) > parseTimestamp(baselineCommittedAt);
}

/** One-line "what changed" summary, e.g. "10/09: 29 → 31 tubs". Empty when nothing moved. */
export function describeChanges(changes: OrderRecoChange[], maxItems = 3): string {
  return changes
    .filter((c) => c.before != null && (c.before !== c.after || c.items.length > 0))
    .map((c) => {
      const date = `${c.deliveryDate.slice(5, 7)}/${c.deliveryDate.slice(8, 10)}`;
      const total = c.before !== c.after ? `${c.before} → ${c.after} tubs` : `${c.after} tubs`;
      const moved = c.items.slice(0, maxItems).map((i) => `${i.item} ${i.before}→${i.after}`);
      const more = c.items.length > maxItems ? ` +${c.items.length - maxItems} more` : "";
      return moved.length ? `${date}: ${total} (${moved.join(", ")}${more})` : `${date}: ${total}`;
    })
    .join(" · ");
}

/** Signed delta label for a chip, e.g. "+2" / "−1"; null when unchanged. */
export function deltaLabel(before: number, after: number): string | null {
  const d = Math.round(after) - Math.round(before);
  if (d === 0) return null;
  return d > 0 ? `+${d}` : `\u2212${Math.abs(d)}`;
}
