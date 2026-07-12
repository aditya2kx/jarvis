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
      .mockResolvedValueOnce([{ value: "120" }]); // max tubs for refresh

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
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot2"))).toBe(true);
  });

  it("submitRestock refuses replace-estimated", async () => {
    const { submitRestock } = await load();
    await expect(
      submitRestock("palmetto", "2026-07-25", "replace-estimated", [], "op@test"),
    ).rejects.toThrow(/replaceEstimatedRestockDate/);
  });
});
