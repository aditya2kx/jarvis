import { kdsBySource, orderQualityDaily } from "@/lib/bq/queries";
import { dateSortKey, formatDate } from "@/lib/format";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { DataTable } from "@/components/tables/DataTable";
import type { ColumnDef } from "@tanstack/react-table";
import type { OrderQualityDailyRow } from "@/lib/bq/queries";

export const revalidate = 600;

export default async function OrderQualityPage() {
  let rows: OrderQualityDailyRow[] = [];
  let bySourceChart: Record<string, unknown>[] = [];
  let error: string | undefined;
  try {
    const [oq, src] = await Promise.all([orderQualityDaily(30), kdsBySource(30)]);
    rows = oq;

    // Pivot per-source rows into one chart series per order_source.
    const bySourceDate = new Map<string, Record<string, unknown>>();
    for (const r of src) {
      const key = formatDate(r.date);
      const entry = bySourceDate.get(key) ?? { date: key };
      entry[r.order_source] = r.kds_p95_min;
      bySourceDate.set(key, entry);
    }
    bySourceChart = Array.from(bySourceDate.values());
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const chartData = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatDate(r.date),
      kds_p95_min: r.kds_p95_min,
      kds_median_min: r.kds_median_min,
    }));

  const sourceKeys = Array.from(
    new Set(bySourceChart.flatMap((r) => Object.keys(r).filter((k) => k !== "date"))),
  );

  const columns: ColumnDef<OrderQualityDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "kds_median_min", header: "Median (min)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "kds_p90_min", header: "p90 (min)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "kds_p95_min", header: "p95 (min)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "kds_p99_min", header: "p99 (min)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "kds_pct_tickets_late", header: "% tickets late", meta: { format: { kind: "pct" } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Order Quality</h1>
        <span className="text-sm text-muted-foreground">Last 30 days · daily</span>
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <LineChartCard
            title="KDS prep time (p95)"
            data={chartData}
            xKey="date"
            series={[
              { key: "kds_p95_min", label: "p95 (min)" },
              { key: "kds_median_min", label: "Median (min)" },
            ]}
          />
          {sourceKeys.length ? (
            <LineChartCard
              title="KDS p95 by order source"
              data={bySourceChart}
              xKey="date"
              series={sourceKeys.map((k) => ({ key: k, label: k }))}
            />
          ) : null}
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Daily percentile detail</h2>
            <DataTable columns={columns} data={rows} />
          </div>
        </>
      )}
    </div>
  );
}
