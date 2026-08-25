import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mutate = vi.fn();
const q = vi.fn();
const dateParam = vi.fn((d: string) => ({ __date: d }));
const intParam = vi.fn((n: number) => n);
const fq = vi.fn((name: string) => `\`${name}\``);

vi.mock("@/lib/bq/client", () => ({
  mutate: (...args: unknown[]) => mutate(...args),
  q: (...args: unknown[]) => q(...args),
  dateParam: (d: string) => dateParam(d),
  intParam: (n: number) => intParam(n),
  timestampParam: (d: Date) => d,
  fq: (name: string) => fq(name),
}));

function routeQ(sql: string): unknown[] {
  if (sql.includes("vw_order_reco_next_dates") && sql.includes("SELECT slot")) {
    return [{ slot: 1 }, { slot: 2 }];
  }
  if (sql.includes("vw_order_reco_next_dates")) {
    return [
      { delivery_date: "2026-07-23" },
      { delivery_date: "2026-07-30" },
    ];
  }
  if (sql.includes("Item = 'TOTAL'")) {
    return [
      { delivery_date: "2026-07-23" },
      { delivery_date: "2026-07-30" },
    ];
  }
  if (sql.includes("CURRENT_DATE('America/Chicago')")) {
    return [{ today: "2026-07-17" }];
  }
  if (sql.includes("MAX(refreshed_at)")) {
    return [{ refreshed_ct: "2026-07-17" }];
  }
  if (sql.includes("HAVING COUNT(*) > 1")) {
    return [{ n: 0 }];
  }
  if (sql.includes("order_reco_max_tubs")) {
    return [{ value: "120" }];
  }
  return [];
}

describe("ensureOrderRecoFresh", () => {
  beforeEach(() => {
    vi.resetModules();
    mutate.mockReset();
    q.mockReset();
    mutate.mockResolvedValue(undefined);
    q.mockImplementation(async (sql: string) => routeQ(sql));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  async function load() {
    return import("@/lib/bq/writes");
  }

  it("no-ops when delivery_dates match and refreshed_at CT day is today", async () => {
    const { ensureOrderRecoFresh } = await load();
    const did = await ensureOrderRecoFresh("palmetto");
    expect(did).toEqual({ status: "fresh" });
    expect(mutate).not.toHaveBeenCalled();
  });

  it("refreshes when materialized delivery_dates diverge from live next dates", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("Item = 'TOTAL'")) {
        // Stale: still bound to Jul 16 restock day while headers show Jul 23/30
        return [{ delivery_date: "2026-07-16" }, { delivery_date: "2026-07-23" }];
      }
      return routeQ(sql);
    });
    const { ensureOrderRecoFresh } = await load();
    const did = await ensureOrderRecoFresh("palmetto");
    expect(did).toEqual({ status: "refreshed" });
    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_order_reco"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot1"))).toBe(true);
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot_n"))).toBe(true);
  });

  it("refreshes when refreshed_at CT day is before today", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("MAX(refreshed_at)")) {
        return [{ refreshed_ct: "2026-07-16" }];
      }
      return routeQ(sql);
    });
    const { ensureOrderRecoFresh } = await load();
    const did = await ensureOrderRecoFresh("palmetto");
    expect(did).toEqual({ status: "refreshed" });
    expect(mutate).toHaveBeenCalled();
  });

  it("enqueues instead of inline refresh when enqueue callback provided", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("MAX(refreshed_at)")) {
        return [{ refreshed_ct: "2026-07-16" }];
      }
      return routeQ(sql);
    });
    const enqueue = vi.fn().mockResolvedValue(undefined);
    const { ensureOrderRecoFresh } = await load();
    const did = await ensureOrderRecoFresh("palmetto", { enqueue });
    expect(did).toEqual({ status: "queued" });
    expect(enqueue).toHaveBeenCalledTimes(1);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("refreshes when duplicate Slot/Item rows exist even if dates match", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("HAVING COUNT(*) > 1")) {
        return [{ n: 5 }];
      }
      return routeQ(sql);
    });
    const { ensureOrderRecoFresh } = await load();
    const did = await ensureOrderRecoFresh("palmetto");
    expect(did).toEqual({ status: "refreshed" });
    expect(mutate).toHaveBeenCalled();
  });
});
