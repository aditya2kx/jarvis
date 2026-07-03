import { forecast, forecastAccuracy } from "@/lib/bq/queries";
import { dateSortKey, formatDate } from "@/lib/format";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { DataTable } from "@/components/tables/DataTable";
import type { ColumnDef } from "@tanstack/react-table";
import type { ForecastRow } from "@/lib/bq/queries";

export const revalidate = 600;

export default async function ForecastPage() {
  let rows: ForecastRow[] = [];
  let accuracyChart: Record<string, unknown>[] = [];
  let error: string | undefined;
  try {
    const [fc, acc] = await Promise.all([forecast(30), forecastAccuracy(30)]);
    rows = fc;
    accuracyChart = [...acc]
      .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
      .map((r) => ({
        date: formatDate(r.date),
        forecast_orders: r.forecast_orders,
        actual_orders: r.actual_orders,
      }));
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const scheduleChart = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatDate(r.date),
      forecast_orders: r.forecast_orders,
      prior_wk_orders: r.prior_wk_orders,
    }));

  const columns: ColumnDef<ForecastRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "dow", header: "Day" },
    { accessorKey: "forecast_orders", header: "Fcst orders", meta: { format: { kind: "number" } } },
    { accessorKey: "prior_wk_orders", header: "Prior wk", meta: { format: { kind: "number" } } },
    { accessorKey: "orders_vs_prior_wk", header: "vs prior wk", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "scheduled_hours", header: "Scheduled hrs", meta: { format: { kind: "number", digits: 1 } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Forecast</h1>
        <span className="text-sm text-muted-foreground">Today + next 30 days</span>
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <LineChartCard
            title="Forecast orders vs prior week"
            data={scheduleChart}
            xKey="date"
            series={[
              { key: "forecast_orders", label: "Forecast" },
              { key: "prior_wk_orders", label: "Prior week" },
            ]}
          />
          <LineChartCard
            title="Forecast accuracy — last 30 days"
            data={accuracyChart}
            xKey="date"
            series={[
              { key: "forecast_orders", label: "Forecast orders" },
              { key: "actual_orders", label: "Actual orders" },
            ]}
          />
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Upcoming schedule</h2>
            <DataTable columns={columns} data={rows} />
          </div>
        </>
      )}
    </div>
  );
}
