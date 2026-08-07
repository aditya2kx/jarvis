/** Order Quality chart series helpers (Issue #225). */

export type OqMetric = "p95" | "avg";

export function parseOqMetric(value: string | string[] | undefined): OqMetric {
  const raw = Array.isArray(value) ? value[0] : value;
  return raw === "avg" ? "avg" : "p95";
}

export function oqMetricLabel(metric: OqMetric): string {
  return metric === "avg" ? "Average (min)" : "p95 (min)";
}

export function oqMetricField(metric: OqMetric): "kds_p95_min" | "kds_avg_min" {
  return metric === "avg" ? "kds_avg_min" : "kds_p95_min";
}

export function buildOqAggregateSeries(metric: OqMetric): { key: string; label: string }[] {
  return [{ key: oqMetricField(metric), label: oqMetricLabel(metric) }];
}

export function buildOqBySourceSeries(sourceKeys: string[]): { key: string; label: string }[] {
  return sourceKeys.map((k) => ({ key: k, label: k }));
}
