import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ACTIVE_BASES } from "@/lib/restock/parse";
import {
  pivotRestockActuals,
  restockActualsColumns,
} from "@/lib/inventory/restockActuals";

describe("pivotRestockActuals", () => {
  it("pivots dates newest-first with ACTIVE_BASES order and TOTAL", () => {
    const rows = pivotRestockActuals([
      { delivery_date: "2026-08-12", item: "Açaí", quantity_tubs: 15 },
      { delivery_date: "2026-08-12", item: "Tropical", quantity_tubs: 20 },
      { delivery_date: "2026-08-03T00:00:00", item: "Açaí", quantity_tubs: 14 },
      { delivery_date: "2026-08-12", item: "Mango", quantity_tubs: 6 },
    ]);

    expect(rows).toHaveLength(2);
    expect(rows[0].date).toBe("2026-08-12");
    expect(rows[1].date).toBe("2026-08-03");
    expect(rows[0]["Açaí"]).toBe(15);
    expect(rows[0].Tropical).toBe(20);
    expect(rows[0].Mango).toBe(6);
    expect(rows[0].Coconut).toBe(0);
    expect(rows[0].TOTAL).toBe(41);
    expect(rows[1].TOTAL).toBe(14);
    expect(Object.keys(rows[0]).filter((k) => k !== "date" && k !== "TOTAL")).toEqual([
      ...ACTIVE_BASES,
    ]);
  });

  it("ignores Blade and other non-active items", () => {
    const rows = pivotRestockActuals([
      { delivery_date: "2026-08-12", item: "Açaí", quantity_tubs: 10 },
      { delivery_date: "2026-08-12", item: "Blade", quantity_tubs: 99 },
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].TOTAL).toBe(10);
    expect(rows[0].Blade).toBeUndefined();
  });

  it("treats null quantity as 0", () => {
    const rows = pivotRestockActuals([
      { delivery_date: "2026-08-12", item: "Açaí", quantity_tubs: null },
      { delivery_date: "2026-08-12", item: "Mango", quantity_tubs: 3 },
    ]);
    expect(rows[0]["Açaí"]).toBe(0);
    expect(rows[0].TOTAL).toBe(3);
  });

  it("returns empty for no rows", () => {
    expect(pivotRestockActuals([])).toEqual([]);
  });

  it("builds Date + bases + TOTAL columns", () => {
    const cols = restockActualsColumns();
    expect(cols[0].accessorKey).toBe("date");
    expect(cols[cols.length - 1].accessorKey).toBe("TOTAL");
    expect(cols).toHaveLength(ACTIVE_BASES.length + 2);
  });
});

vi.mock("server-only", () => ({}));

const q = vi.fn();
const dateParam = vi.fn((d: string) => ({ __date: d }));
const fq = vi.fn((name: string) => `\`${name}\``);

vi.mock("@/lib/bq/client", () => ({
  q: (...args: unknown[]) => q(...args),
  dateParam: (d: string) => dateParam(d),
  fq: (name: string) => fq(name),
}));

describe("restockActuals query", () => {
  beforeEach(() => {
    vi.resetModules();
    q.mockReset();
    dateParam.mockClear();
    q.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("reads inventory_restock_orders in the Period and not estimates", async () => {
    const { restockActuals } = await import("@/lib/bq/queries");
    await restockActuals("palmetto", {
      start: "2026-08-01",
      end: "2026-08-31",
      label: "This month",
      preset: "this_month",
    });
    expect(q).toHaveBeenCalledTimes(1);
    const sql = String(q.mock.calls[0][0]);
    const params = q.mock.calls[0][1] as Record<string, unknown>;
    expect(sql).toContain("inventory_restock_orders");
    expect(sql).toContain("delivery_date BETWEEN @start AND @end");
    expect(sql).not.toContain("inventory_order_reco");
    expect(params.store).toBe("palmetto");
    expect(dateParam).toHaveBeenCalledWith("2026-08-01");
    expect(dateParam).toHaveBeenCalledWith("2026-08-31");
  });
});
