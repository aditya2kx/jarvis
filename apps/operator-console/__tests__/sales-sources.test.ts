import { describe, expect, it } from "vitest";
import {
  SOURCES_NONE,
  normalizeSourceSelection,
  parseBreakdown,
  parseSources,
  serializeSources,
} from "@/lib/filters/sources";
import { pivotSalesChart } from "@/lib/charts/sales-pivot";

describe("parseSources", () => {
  it("treats missing/empty/All as all sources (null)", () => {
    expect(parseSources(undefined)).toBeNull();
    expect(parseSources("")).toBeNull();
    expect(parseSources("All")).toBeNull();
    expect(parseSources("   ")).toBeNull();
  });

  it("treats __none__ as none selected ([])", () => {
    expect(parseSources(SOURCES_NONE)).toEqual([]);
  });

  it("splits comma-separated sources and dedupes/sorts", () => {
    expect(parseSources("Uber Eats,DoorDash")).toEqual(["DoorDash", "Uber Eats"]);
    expect(parseSources("DoorDash,DoorDash")).toEqual(["DoorDash"]);
  });

  it("takes the first value when given an array", () => {
    expect(parseSources(["Register", "Kiosk"])).toEqual(["Register"]);
  });
});

describe("serializeSources", () => {
  it("omits the param for all (null)", () => {
    expect(serializeSources(null)).toBe("");
  });

  it("encodes none as __none__", () => {
    expect(serializeSources([])).toBe(SOURCES_NONE);
  });

  it("joins selected sources", () => {
    expect(serializeSources(["DoorDash", "Uber Eats"])).toBe("DoorDash,Uber Eats");
  });
});

describe("parseBreakdown", () => {
  it("is true only for 1/true", () => {
    expect(parseBreakdown("1")).toBe(true);
    expect(parseBreakdown("true")).toBe(true);
    expect(parseBreakdown("0")).toBe(false);
    expect(parseBreakdown(undefined)).toBe(false);
  });
});

describe("normalizeSourceSelection", () => {
  const opts = ["Register", "DoorDash", "Uber Eats"];

  it("Clear → none ([]), not all", () => {
    expect(normalizeSourceSelection([], opts)).toEqual([]);
  });

  it("Select all → all (null)", () => {
    expect(normalizeSourceSelection([...opts], opts)).toBeNull();
  });

  it("keeps partial selections sorted", () => {
    expect(normalizeSourceSelection(["Uber Eats", "DoorDash"], opts)).toEqual([
      "DoorDash",
      "Uber Eats",
    ]);
  });
});

describe("select-all / clear URL contract", () => {
  it("Select All serializes to an omitted sources param", () => {
    const normalized = normalizeSourceSelection(
      ["Register", "DoorDash", "Uber Eats"],
      ["Register", "DoorDash", "Uber Eats"],
    );
    expect(normalized).toBeNull();
    expect(serializeSources(normalized)).toBe("");
  });

  it("Clear serializes to sources=__none__", () => {
    expect(serializeSources(normalizeSourceSelection([], ["Register"]))).toBe(SOURCES_NONE);
  });
});

describe("pivotSalesChart", () => {
  const rows = [
    { date: "Jun 1", source: "DoorDash", net_sales: 100, orders: 4, items_sold: 8 },
    { date: "Jun 1", source: "Uber Eats", net_sales: 50, orders: 2, items_sold: 3 },
    { date: "Jun 2", source: "DoorDash", net_sales: 80, orders: 3, items_sold: 5 },
  ];

  it("aggregates into a single series when breakdown is off", () => {
    const { data, series } = pivotSalesChart(rows, "net_sales", false, "Net sales");
    expect(series).toEqual([{ key: "net_sales", label: "Net sales" }]);
    expect(data).toEqual([
      { date: "Jun 1", net_sales: 150 },
      { date: "Jun 2", net_sales: 80 },
    ]);
  });

  it("pivots one series per source when breakdown is on", () => {
    const { data, series } = pivotSalesChart(rows, "orders", true, "Orders");
    expect(series.map((s) => s.key)).toEqual(["DoorDash", "Uber Eats"]);
    expect(data.find((r) => r.date === "Jun 1")).toMatchObject({
      DoorDash: 4,
      "Uber Eats": 2,
    });
  });

  it("stacks of breakdown equal the aggregate total", () => {
    const agg = pivotSalesChart(rows, "net_sales", false, "Net sales");
    const brk = pivotSalesChart(rows, "net_sales", true, "Net sales");
    for (const row of agg.data) {
      const wide = brk.data.find((r) => r.date === row.date)!;
      const stack = brk.series.reduce((s, ser) => s + Number(wide[ser.key] || 0), 0);
      expect(stack).toBe(row.net_sales);
    }
  });
});
