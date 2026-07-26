import { salesByGrain, salesSourceOptions, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { pivotSalesChart } from "@/lib/charts/sales-pivot";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterMultiSelect } from "@/components/filters/FilterMultiSelect";
import { FilterPills } from "@/components/filters/FilterPills";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, formatBucket, parseGrain, wantsCustom } from "@/lib/filters/range";
import { parseBreakdown, parseSources, serializeSources } from "@/lib/filters/sources";
import { resolvePageRange } from "@/lib/filters/period";
import type { ColumnDef } from "@tanstack/react-table";
import type { SalesBySourceRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

// Net sales, orders, and items by Square source — reads square_transactions
// (+ item_lines) so the Source multi-select and breakdown toggle work.
// Home/Labor still use vw_model_labor_daily (no source dimension).
export default async function SalesPage({
  searchParams,
}: {
  searchParams: Promise<{
    range?: string;
    from?: string;
    to?: string;
    grain?: string;
    sources?: string;
    breakdown?: string;
  }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const grain = parseGrain(sp.grain);
  const sources = parseSources(sp.sources);
  const breakdown = parseBreakdown(sp.breakdown);
  const showCustomPicker = wantsCustom(sp.range);
  const dateParams: Record<string, string> = win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const sourcesParam = serializeSources(sources);
  const breakdownParam = breakdown ? "1" : "0";

  let rows: SalesBySourceRow[] = [];
  let sourceOptions: string[] = [];
  let goalWeekly: number | undefined;
  let error: string | undefined;
  try {
    const [sales, opts, config] = await Promise.all([
      // Always fetch by-source when breakdown is on; otherwise aggregate.
      salesByGrain(win, grain, sources, breakdown),
      salesSourceOptions(win),
      storeConfig(DEFAULT_STORE),
    ]);
    rows = sales;
    sourceOptions = opts.map((r) => r.source);
    const g = config.find((r) => r.key === "goal_net_sales_weekly");
    goalWeekly = g ? Number(g.value) / 7 : undefined;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const sorted = [...rows].sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1));
  const chartRows = sorted.map((r) => ({
    ...r,
    date: formatBucket(r.date, grain),
  }));

  const netChart = pivotSalesChart(chartRows, "net_sales", breakdown, "Net sales");
  const ordersChart = pivotSalesChart(chartRows, "orders", breakdown, "Orders");
  const itemsChart = pivotSalesChart(chartRows, "items_sold", breakdown, "Items sold");

  // Detail table is always aggregated (one row per bucket) even when charts
  // break down by source — keeps the table readable; charts carry the split.
  const tableRows = (() => {
    if (!breakdown) return sorted;
    const byDate = new Map<string, SalesBySourceRow>();
    for (const r of sorted) {
      const cur = byDate.get(r.date);
      if (!cur) {
        byDate.set(r.date, {
          date: r.date,
          source: null,
          net_sales: Number(r.net_sales) || 0,
          orders: Number(r.orders) || 0,
          items_sold: Number(r.items_sold) || 0,
          avg_order_price: 0,
        });
      } else {
        cur.net_sales += Number(r.net_sales) || 0;
        cur.orders += Number(r.orders) || 0;
        cur.items_sold += Number(r.items_sold) || 0;
      }
    }
    return Array.from(byDate.values()).map((r) => ({
      ...r,
      avg_order_price: r.orders > 0 ? r.net_sales / r.orders : 0,
    }));
  })();

  const columns: ColumnDef<SalesBySourceRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "bucket", grain } } },
    { accessorKey: "net_sales", header: "Net sales", meta: { format: { kind: "dollars" } } },
    { accessorKey: "orders", header: "Orders", meta: { format: { kind: "number" } } },
    { accessorKey: "items_sold", header: "Items", meta: { format: { kind: "number" } } },
    { accessorKey: "avg_order_price", header: "AOV", meta: { format: { kind: "dollars" } } },
  ];

  const showGoal = grain === "day" && sources == null && !breakdown;
  const breakdownExtras: Record<string, string> = {
    range: win.preset,
    grain,
    ...dateParams,
    ...(sourcesParam ? { sources: sourcesParam } : {}),
  };
  const selectedForUi = sources ?? [];

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Sales"
        subtitle={`Net sales, orders, and items sold · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterMultiSelect
              label="Source"
              param="sources"
              selected={selectedForUi}
              options={sourceOptions}
              basePath="/sales"
              extraParams={{
                range: win.preset,
                grain,
                breakdown: breakdownParam,
                ...dateParams,
              }}
            />
            <FilterPills
              label="View"
              param="breakdown"
              value={breakdownParam}
              options={[
                { value: "0", label: "Aggregate" },
                { value: "1", label: "By source" },
              ]}
              basePath="/sales"
              extraParams={breakdownExtras}
            />
            <AggregationSelect
              value={grain}
              basePath="/sales"
              extraParams={{
                range: win.preset,
                breakdown: breakdownParam,
                ...dateParams,
                ...(sourcesParam ? { sources: sourcesParam } : {}),
              }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/sales"
              extraParams={{
                grain,
                breakdown: breakdownParam,
                ...(sourcesParam ? { sources: sourcesParam } : {}),
              }}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/sales"
                from={win.start}
                to={win.end}
                committed={win.preset === "custom"}
                extraParams={{
                  grain,
                  breakdown: breakdownParam,
                  ...(sourcesParam ? { sources: sourcesParam } : {}),
                }}
              />
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
            data={netChart.data}
            xKey="date"
            series={netChart.series}
            stacked={breakdown && netChart.series.length > 1}
            goal={showGoal ? goalWeekly : undefined}
            goalLabel="Weekly goal / 7"
          />
          <BarChartCard
            title={`Orders by ${grain}`}
            data={ordersChart.data}
            xKey="date"
            series={ordersChart.series}
            stacked={breakdown && ordersChart.series.length > 1}
            valueFormat="number"
          />
          <BarChartCard
            title={`Items sold by ${grain}`}
            data={itemsChart.data}
            xKey="date"
            series={itemsChart.series}
            stacked={breakdown && itemsChart.series.length > 1}
            valueFormat="number"
          />
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              {grain === "day" ? "Daily" : grain === "week" ? "Weekly" : "Monthly"} detail
              {sources ? ` · ${sources.length} source${sources.length === 1 ? "" : "s"}` : ""}
            </h2>
            <DataTable columns={columns} data={tableRows} />
          </div>
        </>
      )}
    </div>
  );
}
