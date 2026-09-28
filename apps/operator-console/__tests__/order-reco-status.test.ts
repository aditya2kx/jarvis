import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  awaitedRunLanded,
  deltaLabel,
  describeChanges,
  hasCommittedSince,
  parseTimestamp,
  relativeAgo,
  typicalSeconds,
  type OrderRecoStatus,
} from "@/lib/inventory/orderRecoStatus";

vi.mock("server-only", () => ({}));

const q = vi.fn();
const submitQuery = vi.fn();
vi.mock("@/lib/bq/client", () => ({
  q: (...a: unknown[]) => q(...a),
  submitQuery: (...a: unknown[]) => submitQuery(...a),
  fq: (n: string) => `\`${n}\``,
}));

const base = {
  running_run_id: null,
  running_started_at: null,
  running_trigger: null,
  committed_run_id: "r1",
  committed_at: "2026-09-28T15:21:32.000Z",
  committed_fingerprint: "-674970970581966123",
  failed_run_id: null,
  failed_at: null,
  failed_error: null,
  live_fingerprint: "-674970970581966123",
  last_latency_ms: 18000,
  p95_latency_ms: 21000,
  awaited_status: null,
  last_edit_age_s: null,
};

describe("toStatus", () => {
  it("idle when the painted generation matches live inputs", async () => {
    const { toStatus } = await import("@/lib/bhaga/orderReco");
    expect(toStatus(base).state).toBe("idle");
  });
  it("stale when inputs changed since the painted generation", async () => {
    const { toStatus } = await import("@/lib/bhaga/orderReco");
    expect(toStatus({ ...base, live_fingerprint: "-674970970581966124" }).state).toBe("stale");
  });
  it("stale when nothing has ever committed", async () => {
    const { toStatus } = await import("@/lib/bhaga/orderReco");
    expect(toStatus({ ...base, committed_run_id: null, committed_fingerprint: null }).state).toBe("stale");
  });
  it("running beats failed beats stale", async () => {
    const { toStatus } = await import("@/lib/bhaga/orderReco");
    const failed = { ...base, live_fingerprint: "-674970970581966124", failed_run_id: "f", failed_error: "boom" };
    expect(toStatus(failed).state).toBe("failed");
    expect(toStatus({ ...failed, running_run_id: "x", running_started_at: "t" }).state).toBe("running");
  });
});

describe("orderRecoStatus", () => {
  it("passes the awaited run id (empty when none) and maps its status", async () => {
    const { orderRecoStatus } = await import("@/lib/bhaga/orderReco");
    q.mockResolvedValueOnce([{ ...base, awaited_status: "committed" }]);
    expect((await orderRecoStatus("palmetto", "console-1")).awaitedStatus).toBe("committed");
    expect(q.mock.calls.at(-1)![1]).toEqual({ store: "palmetto", awaitRunId: "console-1" });
    q.mockResolvedValueOnce([base]);
    await orderRecoStatus("palmetto");
    expect(q.mock.calls.at(-1)![1]).toEqual({ store: "palmetto", awaitRunId: "" });
  });
});

describe("requestOrderRecoRefresh", () => {
  beforeEach(() => {
    submitQuery.mockReset().mockResolvedValue("job-1");
  });
  it("submits the procedure without waiting and returns its run id", async () => {
    const { requestOrderRecoRefresh } = await import("@/lib/bhaga/orderReco");
    const { runId } = await requestOrderRecoRefresh({ store: "palmetto", trigger: "capacity", requestedBy: "op" });
    expect(runId).toMatch(/^console-/);
    const [sql, params] = submitQuery.mock.calls[0]!;
    expect(String(sql)).toContain("CALL `sp_refresh_order_reco`");
    expect(params).toMatchObject({ store: "palmetto", trigger: "capacity", by: "op", runId });
  });
});

describe("ensureOrderRecoFresh", () => {
  beforeEach(() => {
    submitQuery.mockReset().mockResolvedValue("job");
  });
  it("requests a refresh only when stale", async () => {
    const { ensureOrderRecoFresh } = await import("@/lib/bhaga/orderReco");
    q.mockResolvedValueOnce([base]);
    expect((await ensureOrderRecoFresh("palmetto", "op")).requested).toBe(false);
    q.mockResolvedValueOnce([{ ...base, live_fingerprint: "9" }]);
    const out = await ensureOrderRecoFresh("palmetto", "op");
    expect(out.requested).toBe(true);
    expect(out.status.state).toBe("running");
    expect(submitQuery).toHaveBeenCalledTimes(1);
  });
  it("waits out an edit whose own refresh may not have started yet", async () => {
    const { ensureOrderRecoFresh } = await import("@/lib/bhaga/orderReco");
    q.mockResolvedValueOnce([{ ...base, live_fingerprint: "9", last_edit_age_s: 3 }]);
    expect((await ensureOrderRecoFresh("palmetto", "op")).requested).toBe(false);
    q.mockResolvedValueOnce([{ ...base, live_fingerprint: "9", last_edit_age_s: 600 }]);
    expect((await ensureOrderRecoFresh("palmetto", "op")).requested).toBe(true);
  });
  it("does not auto-retry a failed run", async () => {
    const { ensureOrderRecoFresh } = await import("@/lib/bhaga/orderReco");
    q.mockResolvedValueOnce([{ ...base, live_fingerprint: "9", failed_run_id: "f" }]);
    expect((await ensureOrderRecoFresh("palmetto", "op")).requested).toBe(false);
  });
});

describe("status helpers", () => {
  const s = (p: Partial<OrderRecoStatus>): OrderRecoStatus => ({
    state: "idle", runId: null, startedAt: null, trigger: null, lastCommittedRunId: null,
    lastCommittedAt: null, lastLatencyMs: null, p95LatencyMs: null, error: null,
    awaitedStatus: null, lastEditAgeS: null, ...p,
  });
  it("awaitedRunLanded waits for its own run, not any newer commit", () => {
    // Gap between submitting the CALL and the procedure's `running` row: an
    // unrelated newer commit must not end the wait (seen in sandbox 2026-09-28).
    const gap = s({ state: "idle", lastCommittedAt: "2026-09-28T16:56:18.000Z", awaitedStatus: null });
    expect(awaitedRunLanded(gap, "console-797a", "2026-09-28T16:55:32.000Z")).toBe(false);
    expect(awaitedRunLanded(s({ awaitedStatus: "running" }), "console-797a", null)).toBe(false);
    expect(awaitedRunLanded(s({ awaitedStatus: "committed" }), "console-797a", null)).toBe(true);
    expect(awaitedRunLanded(s({ awaitedStatus: "superseded" }), "console-797a", null)).toBe(true);
    // Ack without a run id: any newer commit while nothing runs.
    expect(awaitedRunLanded(gap, null, "2026-09-28T16:55:32.000Z")).toBe(true);
    expect(awaitedRunLanded({ ...gap, state: "running" }, null, "2026-09-28T16:55:32.000Z")).toBe(false);
  });
  it("hasCommittedSince compares BQ timestamp strings", () => {
    expect(hasCommittedSince(s({ lastCommittedAt: "2026-09-28T15:21:32.000Z" }), "2026-09-28T15:18:00.000Z")).toBe(true);
    expect(hasCommittedSince(s({ lastCommittedAt: "2026-09-28 15:21:32.1+00" }), "2026-09-28 15:21:32+00")).toBe(true);
    expect(hasCommittedSince(s({ lastCommittedAt: "2026-09-28 15:21:32" }), "2026-09-28 15:21:32")).toBe(false);
    expect(hasCommittedSince(s({ lastCommittedAt: null }), null)).toBe(false);
  });
  it("typicalSeconds prefers p95 and has a floor", () => {
    expect(typicalSeconds({ p95LatencyMs: 21000, lastLatencyMs: 1 })).toBe(21);
    expect(typicalSeconds({ p95LatencyMs: null, lastLatencyMs: null })).toBe(20);
    expect(typicalSeconds({ p95LatencyMs: 100, lastLatencyMs: null })).toBe(5);
  });
  it("describeChanges lists dates whose total or bases moved", () => {
    expect(
      describeChanges([
        { deliveryDate: "2026-10-02", before: 41, after: 41, items: [] },
        { deliveryDate: "2026-10-09", before: 29, after: 31, items: [] },
        { deliveryDate: "2026-10-23", before: null, after: 30, items: [] },
      ]),
    ).toBe("10/09: 29 → 31 tubs");
  });
  it("describeChanges shows the bases when a pin moves tubs under a flat total", () => {
    const items = [
      { item: "Açaí", before: 6, after: 12 },
      { item: "Mango", before: 8, after: 6 },
      { item: "Coconut", before: 5, after: 4 },
      { item: "Matcha", before: 6, after: 5 },
    ];
    expect(describeChanges([{ deliveryDate: "2026-10-16", before: 39, after: 39, items }])).toBe(
      "10/16: 39 tubs (Açaí 6→12, Mango 8→6, Coconut 5→4 +1 more)",
    );
  });
  it("orderRecoLastChange groups TOTAL + moved bases per date", async () => {
    const { orderRecoLastChange } = await import("@/lib/bhaga/orderReco");
    q.mockResolvedValueOnce([
      { deliveryDate: "2026-10-16", item: "TOTAL", before: 39, after: 39 },
      { deliveryDate: "2026-10-16", item: "Açaí", before: 6, after: 12 },
      { deliveryDate: "2026-10-16", item: "Mango", before: 8, after: 6 },
      { deliveryDate: "2026-10-23", item: "TOTAL", before: null, after: 30 },
      { deliveryDate: "2026-10-23", item: "Ube", before: null, after: 1 },
    ]);
    expect(await orderRecoLastChange("palmetto")).toEqual([
      { deliveryDate: "2026-10-16", before: 39, after: 39, items: [
        { item: "Açaí", before: 6, after: 12 }, { item: "Mango", before: 8, after: 6 },
      ] },
      { deliveryDate: "2026-10-23", before: null, after: 30, items: [] },
    ]);
  });
  it("deltaLabel signs the change", () => {
    expect(deltaLabel(7, 6)).toBe("\u22121");
    expect(deltaLabel(3, 12)).toBe("+9");
    expect(deltaLabel(5, 5)).toBeNull();
  });
  it("parses BigQuery TIMESTAMP strings as well as ISO", () => {
    const iso = Date.parse("2026-09-28T16:46:43.699Z");
    expect(Math.abs(parseTimestamp("2026-09-28 16:46:43.699505+00") - iso)).toBeLessThan(1);
    expect(parseTimestamp("2026-09-28T16:46:43.699Z")).toBe(iso);
    expect(relativeAgo("2026-09-28 16:46:43.699505+00", iso + 5 * 60_000)).toBe("5 min ago");
  });
  it("relativeAgo", () => {
    const now = Date.parse("2026-09-28T15:30:00Z");
    expect(relativeAgo("2026-09-28T15:29:30Z", now)).toBe("just now");
    expect(relativeAgo("2026-09-28T15:20:00Z", now)).toBe("10 min ago");
  });
});
