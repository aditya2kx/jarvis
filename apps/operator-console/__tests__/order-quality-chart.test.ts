import { describe, expect, it } from "vitest";
import {
  buildOqAggregateSeries,
  buildOqBySourceSeries,
  parseOqMetric,
  oqMetricField,
} from "@/lib/charts/order-quality";

describe("order-quality chart helpers", () => {
  it("defaults metric to p95", () => {
    expect(parseOqMetric(undefined)).toBe("p95");
    expect(parseOqMetric("avg")).toBe("avg");
    expect(oqMetricField("avg")).toBe("kds_avg_min");
  });

  it("builds one aggregate series for the selected metric", () => {
    expect(buildOqAggregateSeries("p95")).toEqual([
      { key: "kds_p95_min", label: "p95 (min)" },
    ]);
    expect(buildOqAggregateSeries("avg")).toEqual([
      { key: "kds_avg_min", label: "Average (min)" },
    ]);
  });

  it("builds grouped by-source series", () => {
    expect(buildOqBySourceSeries(["POS", "Uber Eats"]).map((s) => s.key)).toEqual([
      "POS",
      "Uber Eats",
    ]);
  });
});
