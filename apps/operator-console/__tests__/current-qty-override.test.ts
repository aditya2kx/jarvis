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

describe("setCurrentQtyOverride / clearCurrentQtyOverride", () => {
  beforeEach(() => {
    vi.resetModules();
    mutate.mockReset();
    q.mockReset();
    mutate.mockResolvedValue(undefined);
    q.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  async function load() {
    return import("@/lib/bq/writes");
  }

  it("rejects negative qty and does not write", async () => {
    const { setCurrentQtyOverride } = await load();
    await expect(
      setCurrentQtyOverride("palmetto", "Mango", -1, "op@test", { skipRefresh: true }),
    ).rejects.toThrow(/quantity must be/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("rejects TOTAL / Blade", async () => {
    const { setCurrentQtyOverride } = await load();
    await expect(
      setCurrentQtyOverride("palmetto", "TOTAL", 1, "op@test", { skipRefresh: true }),
    ).rejects.toThrow(/invalid item/);
    await expect(
      setCurrentQtyOverride("palmetto", "Blade", 1, "op@test", { skipRefresh: true }),
    ).rejects.toThrow(/invalid item/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("MERGEs override then refreshes unless skipRefresh", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("order_reco_max_tubs")) return [{ value: "120" }];
      if (sql.includes("vw_order_reco_next_dates")) return [{ slot: 1 }];
      return [];
    });
    const { setCurrentQtyOverride } = await load();
    await setCurrentQtyOverride("palmetto", "Mango", 12.5, "op@test");
    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(sqls.some((s) => s.includes("MERGE") && s.includes("inventory_current_qty_overrides"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot1"))).toBe(true);
  });

  it("DELETEs override on clear", async () => {
    const { clearCurrentQtyOverride } = await load();
    await clearCurrentQtyOverride("palmetto", "Mango", { skipRefresh: true });
    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(
      sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_current_qty_overrides")),
    ).toBe(true);
  });

  it("batch apply MERGEs each dirty row then refreshes once", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("order_reco_max_tubs")) return [{ value: "120" }];
      if (sql.includes("vw_order_reco_next_dates")) return [{ slot: 1 }];
      return [];
    });
    const { applyCurrentQtyOverrides } = await load();
    await applyCurrentQtyOverrides(
      "palmetto",
      [
        { item: "Mango", quantityUnits: 10 },
        { item: "Acai", quantityUnits: 4 },
      ],
      "op@test",
    );
    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    const merges = sqls.filter(
      (s) => s.includes("MERGE") && s.includes("inventory_current_qty_overrides"),
    );
    expect(merges.length).toBe(2);
    expect(sqls.filter((s) => s.includes("tvf_order_reco_slot1")).length).toBe(1);
  });
});
