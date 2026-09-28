import "server-only";
import { randomUUID } from "node:crypto";
import { fq, q, submitQuery } from "@/lib/bq/client";
import { FEATURES } from "@/lib/config/features";
import { triggerOrderRecoRefresh } from "@/lib/bhaga/recompute";
import type { OrderRecoChange, OrderRecoStatus } from "@/lib/inventory/orderRecoStatus";

/** A `running` row older than this with no terminal row is a dead run, not a live one. */
const RUNNING_TTL_MINUTES = 5;
/** Page-open self-heal waits this long after the last input edit (its refresh is in flight). */
const EDIT_REFRESH_GRACE_S = 60;

/**
 * Start one atomic order-reco refresh (core/migrations/081_sp_refresh_order_reco.sql)
 * and return immediately. Safe to call on every edit: concurrent runs resolve
 * to one committed generation, and a run re-checks its inputs after committing,
 * so the latest edit always wins.
 */
export async function requestOrderRecoRefresh(opts: {
  store: string;
  trigger: string;
  requestedBy: string;
}): Promise<{ runId: string | null }> {
  if (FEATURES.orderRecoLegacy) {
    await triggerOrderRecoRefresh(opts.store);
    return { runId: null };
  }
  const runId = `console-${randomUUID()}`;
  await submitQuery(
    `CALL ${fq("sp_refresh_order_reco")}(@store, @trigger, @by, @runId, FALSE, NULL, NULL, NULL)`,
    { store: opts.store, trigger: opts.trigger, by: opts.requestedBy, runId },
  );
  return { runId };
}

type StatusRow = {
  running_run_id: string | null;
  running_started_at: string | null;
  running_trigger: string | null;
  committed_run_id: string | null;
  committed_at: string | null;
  committed_fingerprint: string | null;
  failed_run_id: string | null;
  failed_at: string | null;
  failed_error: string | null;
  live_fingerprint: string | null;
  last_latency_ms: number | null;
  p95_latency_ms: number | null;
  awaited_status: string | null;
  last_edit_age_s: number | null;
};

/**
 * Current refresh state for the Inventory page banner and pollers (one query).
 * `awaitRunId` is the run a tab's own edit started: between submitting the CALL
 * and the procedure writing its `running` row there is no trace of it, so "a
 * newer commit exists" is not proof that this edit's numbers have landed.
 */
export async function orderRecoStatus(store: string, awaitRunId?: string | null): Promise<OrderRecoStatus> {
  const [row] = await q<StatusRow>(
    `WITH runs AS (
       SELECT * FROM ${fq("inventory_order_reco_runs")}
       WHERE store = @store AND event_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
     ),
     latest AS (
       SELECT * FROM runs
       QUALIFY ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY event_at DESC) = 1
     ),
     running AS (
       SELECT run_id, event_at, trigger FROM latest
       WHERE status = 'running'
         AND event_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL ${RUNNING_TTL_MINUTES} MINUTE)
       ORDER BY event_at DESC LIMIT 1
     ),
     committed AS (
       SELECT run_id, refreshed_at, inputs_fingerprint FROM runs
       WHERE status = 'committed' ORDER BY event_at DESC LIMIT 1
     ),
     failed AS (
       SELECT run_id, event_at, error FROM latest
       WHERE status = 'failed'
         AND event_at > COALESCE((SELECT refreshed_at FROM committed), TIMESTAMP '1970-01-01')
       ORDER BY event_at DESC LIMIT 1
     ),
     latency AS (
       SELECT run_id, TIMESTAMP_DIFF(MIN(IF(status = 'committed', event_at, NULL)),
                                     MIN(IF(status = 'running', event_at, NULL)), MILLISECOND) AS ms,
              MIN(IF(status = 'committed', event_at, NULL)) AS committed_ts
       FROM runs GROUP BY run_id
       HAVING ms IS NOT NULL
       ORDER BY committed_ts DESC LIMIT 20
     )
     SELECT
       (SELECT run_id FROM running) AS running_run_id,
       FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E3SZ', (SELECT event_at FROM running)) AS running_started_at,
       (SELECT trigger FROM running) AS running_trigger,
       (SELECT run_id FROM committed) AS committed_run_id,
       FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E3SZ', (SELECT refreshed_at FROM committed)) AS committed_at,
       CAST((SELECT inputs_fingerprint FROM committed) AS STRING) AS committed_fingerprint,
       (SELECT run_id FROM failed) AS failed_run_id,
       FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E3SZ', (SELECT event_at FROM failed)) AS failed_at,
       (SELECT error FROM failed) AS failed_error,
       CAST((SELECT inputs_fingerprint FROM ${fq("vw_order_reco_inputs_fingerprint")} WHERE store = @store) AS STRING) AS live_fingerprint,
       (SELECT ms FROM latency ORDER BY committed_ts DESC LIMIT 1) AS last_latency_ms,
       (SELECT APPROX_QUANTILES(ms, 100)[OFFSET(95)] FROM latency) AS p95_latency_ms,
       (SELECT status FROM latest WHERE run_id = @awaitRunId) AS awaited_status,
       (SELECT TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(edited_at), SECOND)
          FROM ${fq("inventory_edit_log")}
          WHERE store = @store AND edited_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)) AS last_edit_age_s`,
    { store, awaitRunId: awaitRunId ?? "" },
  );
  return toStatus(row);
}

export function toStatus(row: StatusRow | undefined): OrderRecoStatus {
  const r = row ?? ({} as StatusRow);
  const stale =
    r.committed_run_id == null ||
    String(r.live_fingerprint ?? "") !== String(r.committed_fingerprint ?? "");
  const state = r.running_run_id
    ? "running"
    : r.failed_run_id
      ? "failed"
      : stale
        ? "stale"
        : "idle";
  return {
    state,
    runId: r.running_run_id ?? r.failed_run_id ?? r.committed_run_id ?? null,
    startedAt: r.running_started_at ?? null,
    trigger: r.running_trigger ?? null,
    lastCommittedRunId: r.committed_run_id ?? null,
    lastCommittedAt: r.committed_at ?? null,
    lastLatencyMs: r.last_latency_ms == null ? null : Number(r.last_latency_ms),
    p95LatencyMs: r.p95_latency_ms == null ? null : Number(r.p95_latency_ms),
    error: r.failed_error ?? null,
    awaitedStatus: (r.awaited_status as OrderRecoStatus["awaitedStatus"]) ?? null,
    lastEditAgeS: r.last_edit_age_s == null ? null : Number(r.last_edit_age_s),
  };
}

/**
 * Page-load self-heal: when inputs changed since the painted generation (new
 * day, nightly closings, an edit whose refresh failed to start) and nothing is
 * running, start a refresh. A failed run is surfaced for an explicit Retry
 * instead of being retried on every page load.
 */
export async function ensureOrderRecoFresh(
  store: string,
  requestedBy: string,
): Promise<{ status: OrderRecoStatus; requested: boolean }> {
  const status = await orderRecoStatus(store);
  if (status.state !== "stale") return { status, requested: false };
  // The edit that made inputs stale started its own refresh; its `running` row
  // can lag the submit by seconds, and the drawer's router.refresh() lands here
  // inside that gap. Requesting again would only supersede the operator's run.
  if (status.lastEditAgeS != null && status.lastEditAgeS < EDIT_REFRESH_GRACE_S) {
    return { status, requested: false };
  }
  const { runId } = await requestOrderRecoRefresh({ store, trigger: "page-open-stale", requestedBy });
  return {
    status: { ...status, state: "running", runId, startedAt: new Date().toISOString(), trigger: "page-open-stale" },
    requested: true,
  };
}

/**
 * Per-date order tubs of the latest committed generation vs the one before it:
 * the TOTAL plus every base that moved. Capacity is a hard cap, so a pin often
 * shifts bases while the total stays put — the bases are the change.
 */
export async function orderRecoLastChange(
  store: string,
): Promise<OrderRecoChange[]> {
  const rows = await q<{ deliveryDate: string; item: string; before: number | null; after: number }>(
    `WITH gens AS (
       SELECT run_id, refreshed_at FROM ${fq("inventory_order_reco_runs")}
       WHERE store = @store AND status = 'committed'
         AND event_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
       ORDER BY event_at DESC LIMIT 2
     ),
     tubs AS (
       SELECT h.refreshed_at, CAST(h.delivery_date AS STRING) AS delivery_date, h.Item AS item,
              h.\`Order Tubs\` AS tubs, h._ord AS ord
       FROM ${fq("inventory_order_reco_history")} h
       JOIN gens g USING (run_id, refreshed_at)
       WHERE h.store = @store
         AND h.refreshed_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 14 DAY)
     ),
     latest AS (SELECT * FROM tubs WHERE refreshed_at = (SELECT MAX(refreshed_at) FROM gens)),
     prior AS (SELECT * FROM tubs WHERE refreshed_at = (SELECT MIN(refreshed_at) FROM gens))
     SELECT l.delivery_date AS deliveryDate, l.item,
            IF((SELECT COUNT(*) FROM gens) < 2, NULL, p.tubs) AS before,
            l.tubs AS after
     FROM latest l LEFT JOIN prior p USING (delivery_date, item)
     WHERE l.item = 'TOTAL' OR p.tubs IS DISTINCT FROM l.tubs
     ORDER BY l.delivery_date, ABS(COALESCE(l.tubs - p.tubs, 0)) DESC, l.ord`,
    { store },
  );
  const byDate = new Map<string, OrderRecoChange>();
  for (const r of rows) {
    const c = byDate.get(r.deliveryDate) ?? { deliveryDate: r.deliveryDate, before: null, after: 0, items: [] };
    if (r.item === "TOTAL") {
      c.before = r.before == null ? null : Number(r.before);
      c.after = Number(r.after);
    } else if (r.before != null) {
      c.items.push({ item: r.item, before: Number(r.before), after: Number(r.after) });
    }
    byDate.set(r.deliveryDate, c);
  }
  return [...byDate.values()];
}
