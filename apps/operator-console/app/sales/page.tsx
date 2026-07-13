import { laborByGrain, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, formatBucket, parseGrain, wantsCustom } from "@/lib/filters/range";
import { resolvePageRange } from "@/lib/filters/period";
import type { ColumnDef } from "@tanstack/react-table";
import type { LaborDailyRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

// Net sales, orders, and items — the sales-facing subset of
// vw_model_labor_daily (same source the old Grafana "Daily Sales" section
// read; no separate sales view exists yet).
export default async function SalesPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; from?: string; to?: string; grain?: string }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const grain = parseGrain(sp.grain);
  const showCustomPicker = wantsCustom(sp.range);
  const dateParams: Record<string, string> = win.preset === "custom" ? { from: win.start, to: win.end } : {};

  let rows: LaborDailyRow[] = [];
  let goalWeekly: number | undefined;
  let error: string | undefined;
  try {
    const [labor, config] = await Promise.all([laborByGrain(win, grain), storeConfig(DEFAULT_STORE)]);
    rows = labor;
    const g = config.find((r) => r.key === "goal_net_sales_weekly");
    goalWeekly = g ? Number(g.value) / 7 : undefined; // daily equivalent for the daily chart
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const chartData = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatBucket(r.date, grain),
      net_sales: r.net_sales,
      orders: r.orders,
      items_sold: r.items_sold,
    }));

  const columns: ColumnDef<LaborDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "bucket", grain } } },
    { accessorKey: "net_sales", header: "Net sales", meta: { format: { kind: "dollars" } } },
    { accessorKey: "orders", header: "Orders", meta: { format: { kind: "number" } } },
    { accessorKey: "items_sold", header: "Items", meta: { format: { kind: "number" } } },
    { accessorKey: "avg_order_price", header: "AOV", meta: { format: { kind: "dollars" } } },
  ];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Sales"
        subtitle={`Net sales, orders, and items sold · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <AggregationSelect value={grain} basePath="/sales" extraParams={{ range: win.preset, ...dateParams }} />
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/sales"
              extraParams={{ grain }}
            />
            {showCustomPicker ? (
              <DateRangePicker basePath="/sales" from={win.start} to={win.end} extraParams={{ grain }} />
            ) : null}
          </>
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <BarChartCard
            title={`Net sales by ${grain}`}
            data={chartData}
            xKey="date"
            series={[{ key: "net_sales", label: "Net sales" }]}
            // A fixed daily-equivalent reference line only reads correctly
            // once a bar already represents one day — weekly/monthly bars
            // aggregate a variable number of days, so no single number is a
            // fair goal line for them (a 28- vs 31-day month, in particular).
            goal={grain === "day" ? goalWeekly : undefined}
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
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              {grain === "day" ? "Daily" : grain === "week" ? "Weekly" : "Monthly"} detail
            </h2>
            <DataTable columns={columns} data={rows} />
          </div>
        </>
      )}
    </div>
  );
}
