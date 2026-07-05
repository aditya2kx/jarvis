import { forecast, forecastAccuracy } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatDate } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { RangeFilter, parseRange } from "@/components/filters/RangeFilter";
import { FilterPills } from "@/components/filters/FilterPills";
import { Badge } from "@/components/ui/badge";
import type { ColumnDef } from "@tanstack/react-table";
import type { ForecastRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

type Metric = "orders" | "items";

function parseMetric(value: string | string[] | undefined): Metric {
  const v = Array.isArray(value) ? value[0] : value;
  return v === "items" ? "items" : "orders";
}

// Mean absolute percentage error over forecastAccuracy rows for the selected
// metric — skips days with no actual (can't divide by zero, not "0% error").
function mape(rows: { forecast: number; actual: number }[]): number | undefined {
  const usable = rows.filter((r) => r.actual);
  if (!usable.length) return undefined;
  const sum = usable.reduce((s, r) => s + Math.abs(r.actual - r.forecast) / r.actual, 0);
  return (sum / usable.length) * 100;
}

export default async function ForecastPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; metric?: string }>;
}) {
  const sp = await searchParams;
  const range = parseRange(sp.range);
  const metric = parseMetric(sp.metric);
  const forecastKey = metric === "orders" ? "forecast_orders" : "forecast_items";
  const priorKey = metric === "orders" ? "prior_wk_orders" : "prior_wk_items";
  const actualKey = metric === "orders" ? "actual_orders" : "actual_items";

  let rows: ForecastRow[] = [];
  let accuracyChart: Record<string, unknown>[] = [];
  let mapePct: number | undefined;
  let error: string | undefined;
  try {
    const [fc, acc] = await Promise.all([forecast(range), forecastAccuracy(range)]);
    rows = fc;
    accuracyChart = [...acc]
      .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
      .map((r) => ({
        date: formatDate(r.date),
        forecast: r[forecastKey] as number,
        actual: r[actualKey] as number,
      }));
    mapePct = mape(acc.map((r) => ({ forecast: r[forecastKey] as number, actual: r[actualKey] as number })));
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const scheduleChart = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatDate(r.date),
      forecast: r[forecastKey] as number,
      prior_wk: r[priorKey] as number,
    }));

  const metricLabel = metric === "orders" ? "orders" : "items";

  const columns: ColumnDef<ForecastRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "dow", header: "Day" },
    { accessorKey: forecastKey, header: `Fcst ${metricLabel}`, meta: { format: { kind: "number" } } },
    { accessorKey: priorKey, header: "Prior wk", meta: { format: { kind: "number" } } },
    {
      accessorKey: metric === "orders" ? "orders_vs_prior_wk" : "items_vs_prior_wk",
      header: "vs prior wk",
      meta: { format: { kind: "number", digits: 1 } },
    },
    { accessorKey: "scheduled_hours", header: "Scheduled hrs", meta: { format: { kind: "number", digits: 1 } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Forecast"
        subtitle={`Forecast vs prior week and accuracy · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterPills
              label="Metric"
              param="metric"
              value={metric}
              options={[
                { value: "orders", label: "Orders" },
                { value: "items", label: "Items" },
              ]}
              basePath="/forecast"
              extraParams={{ range: String(range) }}
            />
            <RangeFilter basePath="/forecast" value={range} extraParams={{ metric }} />
            {mapePct != null ? (
              <Badge variant={mapePct <= 15 ? "default" : mapePct <= 30 ? "secondary" : "destructive"}>
                MAPE {mapePct.toFixed(1)}%
              </Badge>
            ) : null}
          </>
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <LineChartCard
            title={`Forecast ${metricLabel} vs prior week`}
            data={scheduleChart}
            xKey="date"
            series={[
              { key: "forecast", label: "Forecast" },
              { key: "prior_wk", label: "Prior week" },
            ]}
          />
          <LineChartCard
            title={`Forecast accuracy (${metricLabel}) — last ${range} days`}
            data={accuracyChart}
            xKey="date"
            series={[
              { key: "forecast", label: `Forecast ${metricLabel}` },
              { key: "actual", label: `Actual ${metricLabel}` },
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
