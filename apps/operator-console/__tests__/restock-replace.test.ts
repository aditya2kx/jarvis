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

describe("replaceEstimatedRestockDate", () => {
  beforeEach(() => {
    vi.resetModules();
    mutate.mockReset();
    q.mockReset();
    mutate.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  async function load() {
    return import("@/lib/bq/writes");
  }

  it("rejects when from and to are the same", async () => {
    const { replaceEstimatedRestockDate } = await load();
    await expect(
      replaceEstimatedRestockDate("palmetto", "2026-07-23", "2026-07-23", "op@test"),
    ).rejects.toThrow(/must differ/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("rejects when fromDate is not on the schedule", async () => {
    q.mockResolvedValueOnce([{ n: 0 }]); // schedule count
    const { replaceEstimatedRestockDate } = await load();
    await expect(
      replaceEstimatedRestockDate("palmetto", "2026-07-23", "2026-07-25", "op@test"),
    ).rejects.toThrow(/not on the restock schedule/);
  });

  it("rejects when fromDate has Actuals", async () => {
    q.mockResolvedValueOnce([{ n: 1 }]) // on schedule
      .mockResolvedValueOnce([{ n: 3 }]); // has orders
    const { replaceEstimatedRestockDate } = await load();
    await expect(
      replaceEstimatedRestockDate("palmetto", "2026-07-16", "2026-07-25", "op@test"),
    ).rejects.toThrow(/has Actuals/);
  });

  it("happy path: deletes schedule+orders for from, MERGEs to, refreshes reco", async () => {
    q.mockResolvedValueOnce([{ n: 1 }]) // on schedule
      .mockResolvedValueOnce([{ n: 0 }]) // no actuals
      .mockImplementation(async (sql: string) => {
        if (sql.includes("order_reco_max_tubs")) return [{ value: "120" }];
        if (sql.includes("vw_order_reco_next_dates")) return [{ slot: 1 }, { slot: 2 }];
        return [];
      });

    const { replaceEstimatedRestockDate } = await load();
    await replaceEstimatedRestockDate("palmetto", "2026-07-23", "2026-07-25", "op@test");

    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_restock_schedule"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_restock_orders"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("MERGE") && s.includes("inventory_restock_schedule"))).toBe(true);
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot1"))).toBe(true);
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot_n"))).toBe(true);
  });

  it("submitRestock refuses replace-estimated", async () => {
    const { submitRestock } = await load();
    await expect(
      submitRestock("palmetto", "2026-07-25", "replace-estimated", [], "op@test"),
    ).rejects.toThrow(/replaceEstimatedRestockDate/);
  });
});

describe("moveRestockDate", () => {
  beforeEach(() => {
    vi.resetModules();
    mutate.mockReset();
    q.mockReset();
    mutate.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  async function load() {
    return import("@/lib/bq/writes");
  }

  it("rejects when from and to are the same", async () => {
    const { moveRestockDate } = await load();
    await expect(
      moveRestockDate("palmetto", "2026-08-17", "2026-08-17", "op@test"),
    ).rejects.toThrow(/must differ/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("moves Actuals from→to then refreshes reco", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("COUNT(*)") && sql.includes("inventory_restock_schedule")) {
        return [{ n: 1 }];
      }
      if (sql.includes("FROM") && sql.includes("inventory_restock_orders") && sql.includes("SELECT item")) {
        return [
          { item: "Açaí", quantity_tubs: 27 },
          { item: "Mango", quantity_tubs: 21 },
        ];
      }
      if (sql.includes("inventory_order_tub_overrides") && sql.includes("SELECT item")) {
        return [];
      }
      if (sql.includes("order_reco_max_tubs")) return [{ value: "120" }];
      if (sql.includes("vw_order_reco_next_dates")) return [{ slot: 1 }, { slot: 2 }];
      return [];
    });

    const { moveRestockDate } = await load();
    await moveRestockDate("palmetto", "2026-08-17", "2026-08-20", "op@test");

    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_restock_schedule"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("MERGE") && s.includes("inventory_restock_schedule"))).toBe(true);
    expect(sqls.some((s) => s.includes("INSERT INTO") && s.includes("inventory_restock_orders"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot1"))).toBe(true);
  });
});

describe("removeRestockDate", () => {
  beforeEach(() => {
    vi.resetModules();
    mutate.mockReset();
    q.mockReset();
    mutate.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  async function load() {
    return import("@/lib/bq/writes");
  }

  it("rejects unknown date", async () => {
    q.mockResolvedValueOnce([{ n: 0 }]);
    const { removeRestockDate } = await load();
    await expect(removeRestockDate("palmetto", "2026-08-17", "op@test")).rejects.toThrow(
      /not on the restock schedule/,
    );
  });

  it("clears schedule + orders and refreshes", async () => {
    q.mockResolvedValueOnce([{ n: 1 }]).mockImplementation(async (sql: string) => {
      if (sql.includes("order_reco_max_tubs")) return [{ value: "120" }];
      if (sql.includes("vw_order_reco_next_dates")) return [{ slot: 1 }];
      return [];
    });
    const { removeRestockDate } = await load();
    await removeRestockDate("palmetto", "2026-08-17", "op@test");
    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_restock_schedule"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_restock_orders"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot1"))).toBe(true);
  });
});
