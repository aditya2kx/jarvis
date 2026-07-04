import { laborDaily, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatDate } from "@/lib/format";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { RangeFilter, parseRange } from "@/components/filters/RangeFilter";
import type { ColumnDef } from "@tanstack/react-table";
import type { LaborDailyRow } from "@/lib/bq/queries";

export const revalidate = 600;

// Net sales, orders, and items — the sales-facing subset of
// vw_model_labor_daily (same source the old Grafana "Daily Sales" section
// read; no separate sales view exists yet).
export default async function SalesPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string }>;
}) {
  const range = parseRange((await searchParams).range);

  let rows: LaborDailyRow[] = [];
  let goalWeekly: number | undefined;
  let error: string | undefined;
  try {
    const [labor, config] = await Promise.all([laborDaily(range), storeConfig(DEFAULT_STORE)]);
    rows = labor;
    const g = config.find((r) => r.key === "goal_net_sales_weekly");
    goalWeekly = g ? Number(g.value) / 7 : undefined; // daily equivalent for the daily chart
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const chartData = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatDate(r.date),
      net_sales: r.net_sales,
      orders: r.orders,
      items_sold: r.items_sold,
    }));

  const columns: ColumnDef<LaborDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "net_sales", header: "Net sales", meta: { format: { kind: "dollars" } } },
    { accessorKey: "orders", header: "Orders", meta: { format: { kind: "number" } } },
    { accessorKey: "items_sold", header: "Items", meta: { format: { kind: "number" } } },
    { accessorKey: "avg_order_price", header: "AOV", meta: { format: { kind: "dollars" } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Sales</h1>
        <RangeFilter basePath="/sales" value={range} />
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <BarChartCard
            title="Net sales by day"
            data={chartData}
            xKey="date"
            series={[{ key: "net_sales", label: "Net sales" }]}
            goal={goalWeekly}
            goalLabel="Weekly goal / 7"
          />
          <LineChartCard
            title="Orders & items sold"
            data={chartData}
            xKey="date"
            series={[
              { key: "orders", label: "Orders" },
              { key: "items_sold", label: "Items sold" },
            ]}
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
