import { laborDaily, storeConfig, payrollPeriod } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatDate } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { RangeFilter, parseRange } from "@/components/filters/RangeFilter";
import type { ColumnDef } from "@tanstack/react-table";
import type { LaborDailyRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

function goalFromConfig(rows: { key: string; value: string }[], key: string): number | undefined {
  const row = rows.find((r) => r.key === key);
  return row ? Number(row.value) : undefined;
}

export default async function LaborPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string }>;
}) {
  const range = parseRange((await searchParams).range);

  let rows: LaborDailyRow[] = [];
  let goalLaborPct: number | undefined;
  let hoursPerPerson: { employee: string; hours: number }[] = [];
  let error: string | undefined;
  try {
    const [labor, config, period] = await Promise.all([
      laborDaily(range),
      storeConfig(DEFAULT_STORE),
      payrollPeriod(1),
    ]);
    rows = labor;
    goalLaborPct = goalFromConfig(config, "goal_labor_pct_max");
    const openPeriod = period.find((p) => p.is_open) ?? period[0];
    hoursPerPerson = period
      .filter((p) => p.period_start === openPeriod?.period_start)
      .map((p) => ({ employee: p.employee, hours: p.hours_worked }))
      .sort((a, b) => b.hours - a.hours);
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
      // Throughput & saturation: orders (and items) produced per labor hour —
      // the inverse of "hrs/item" already on the daily view, expressed the
      // way an operator reads capacity ("how much did each labor-hour get
      // through today"), no new BQ column needed.
      orders_per_hour: r.total_hours ? Number((r.orders / r.total_hours).toFixed(2)) : null,
      items_per_hour: r.total_hours ? Number((r.items_sold / r.total_hours).toFixed(2)) : null,
      // Part-time/full-time split, derived from vw_model_labor_daily's own
      // fulltime_pct ratio — no separate PT/FT hour columns exist upstream.
      fulltime_hours:
        r.total_hours != null && r.fulltime_pct != null
          ? Number((r.total_hours * r.fulltime_pct).toFixed(1))
          : null,
      parttime_hours:
        r.total_hours != null && r.fulltime_pct != null
          ? Number((r.total_hours * (1 - r.fulltime_pct)).toFixed(1))
          : null,
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
      <PageHeader
        title="Labor"
        subtitle={`Hours, labor %, and throughput · ${storeDisplayName(DEFAULT_STORE)}`}
        right={<RangeFilter basePath="/labor" value={range} />}
      />

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

          <div className="grid gap-4 md:grid-cols-2">
            <BarChartCard
              title="Shift hours — stacked (part-time vs full-time)"
              data={chartData}
              xKey="date"
              stacked
              series={[
                { key: "parttime_hours", label: "Part-time" },
                { key: "fulltime_hours", label: "Full-time" },
              ]}
            />
            <LineChartCard
              title="Throughput & saturation (orders, items per labor hour)"
              data={chartData}
              xKey="date"
              series={[
                { key: "orders_per_hour", label: "Orders/hr" },
                { key: "items_per_hour", label: "Items/hr" },
              ]}
            />
          </div>

          <BarChartCard
            title="Total labor hours by day"
            data={chartData}
            xKey="date"
            series={[{ key: "total_hours", label: "Hours" }]}
          />

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Hours per person — current pay period
            </h2>
            {hoursPerPerson.length ? (
              <div className="flex flex-col divide-y divide-border rounded-md border">
                {hoursPerPerson.map((p) => (
                  <div
                    key={p.employee}
                    className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                  >
                    <span>{p.employee}</span>
                    <span className="font-medium">{p.hours.toFixed(1)} hrs</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No open pay period found.</p>
            )}
          </div>

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Daily detail</h2>
            <DataTable columns={columns} data={rows} />
          </div>
        </>
      )}
    </div>
  );
}
