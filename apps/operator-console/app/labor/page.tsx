import { laborDaily, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatDate } from "@/lib/format";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { DataTable } from "@/components/tables/DataTable";
import type { ColumnDef } from "@tanstack/react-table";
import type { LaborDailyRow } from "@/lib/bq/queries";

export const revalidate = 600;

function goalFromConfig(rows: { key: string; value: string }[], key: string): number | undefined {
  const row = rows.find((r) => r.key === key);
  return row ? Number(row.value) : undefined;
}

export default async function LaborPage() {
  let rows: LaborDailyRow[] = [];
  let goalLaborPct: number | undefined;
  let error: string | undefined;
  try {
    const [labor, config] = await Promise.all([
      laborDaily(30),
      storeConfig(DEFAULT_STORE),
    ]);
    rows = labor;
    goalLaborPct = goalFromConfig(config, "goal_labor_pct_max");
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const chartData = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatDate(r.date),
      labor_pct: r.labor_pct != null ? Number((r.labor_pct * 100).toFixed(1)) : null,
      hourly_pct: r.hourly_pct != null ? Number((r.hourly_pct * 100).toFixed(1)) : null,
      hours_per_item: r.hours_per_item,
      total_hours: r.total_hours,
      net_sales: r.net_sales,
    }));

  const columns: ColumnDef<LaborDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "net_sales", header: "Net sales", meta: { format: { kind: "dollars" } } },
    { accessorKey: "total_labor_cost", header: "Labor cost", meta: { format: { kind: "dollars" } } },
    { accessorKey: "labor_pct", header: "Labor %", meta: { format: { kind: "pct" } } },
    { accessorKey: "total_hours", header: "Hours", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "hours_per_item", header: "Hrs/item", meta: { format: { kind: "number", digits: 3 } } },
    { accessorKey: "orders", header: "Orders", meta: { format: { kind: "number" } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Labor</h1>
        <span className="text-sm text-muted-foreground">Last 30 days · daily</span>
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <LineChartCard
              title="Labor % of net sales"
              data={chartData}
              xKey="date"
              series={[
                { key: "labor_pct", label: "Total labor %" },
                { key: "hourly_pct", label: "Hourly labor %" },
              ]}
              goal={goalLaborPct != null ? goalLaborPct * 100 : undefined}
              goalLabel="Goal"
            />
            <LineChartCard
              title="Hours per item"
              data={chartData}
              xKey="date"
              series={[{ key: "hours_per_item", label: "Hrs/item" }]}
            />
          </div>

          <BarChartCard
            title="Total labor hours by day"
            data={chartData}
            xKey="date"
            series={[{ key: "total_hours", label: "Hours" }]}
          />

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Daily detail</h2>
            <DataTable columns={columns} data={rows} />
          </div>
        </>
      )}
    </div>
  );
}
