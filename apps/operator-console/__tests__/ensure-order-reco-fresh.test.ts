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
  fq: (name: string) => fq(name),
}));

function routeQ(sql: string): unknown[] {
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
    expect(did).toBe(false);
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
    expect(did).toBe(true);
    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_order_reco"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot1"))).toBe(true);
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot2"))).toBe(true);
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
    expect(did).toBe(true);
    expect(mutate).toHaveBeenCalled();
  });
});
