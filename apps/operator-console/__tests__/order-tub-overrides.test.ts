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

describe("replaceOrderTubOverrides", () => {
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

  it("rejects when Manual sum exceeds capacity and does not write", async () => {
    q.mockResolvedValueOnce([{ value: "120" }]); // order_reco_max_tubs
    const { replaceOrderTubOverrides } = await load();
    await expect(
      replaceOrderTubOverrides(
        "palmetto",
        "2026-08-20",
        [{ item: "Mango", quantityTubs: 121 }],
        "op@test",
        { skipRefresh: true },
      ),
    ).rejects.toThrow(/Manual Order Tubs sum \(121\) exceeds capacity \(120\)/);
    expect(mutate).not.toHaveBeenCalled();
  });

  it("writes pins under capacity then refreshes unless skipRefresh", async () => {
    q.mockImplementation(async (sql: string) => {
      if (sql.includes("order_reco_max_tubs")) return [{ value: "120" }];
      if (sql.includes("vw_order_reco_next_dates")) return [{ slot: 1 }, { slot: 2 }];
      return [];
    });
    const { replaceOrderTubOverrides } = await load();
    await replaceOrderTubOverrides(
      "palmetto",
      "2026-08-20",
      [{ item: "Mango", quantityTubs: 0 }],
      "op@test",
    );
    const sqls = mutate.mock.calls.map((c) => String(c[0]));
    expect(sqls.some((s) => s.includes("DELETE FROM") && s.includes("inventory_order_tub_overrides"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("INSERT INTO") && s.includes("inventory_order_tub_overrides"))).toBe(
      true,
    );
    expect(sqls.some((s) => s.includes("tvf_order_reco_slot1"))).toBe(true);
  });
});
