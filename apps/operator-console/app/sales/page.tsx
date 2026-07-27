import { salesByGrain, salesSourceOptions, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { fillSalesSpine, mergePriorSeries, pivotSalesChart } from "@/lib/charts/sales-pivot";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterMultiSelect } from "@/components/filters/FilterMultiSelect";
import { FilterPills } from "@/components/filters/FilterPills";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import {
  RANGE_PRESETS,
  enumerateBucketStarts,
  formatBucket,
  priorWindow,
  wantsCustom,
} from "@/lib/filters/range";
import {
  assertModeFilterCoherence,
  parseChartMode,
  parseCompare,
} from "@/lib/filters/chart-mode";
import { parseBreakdown, parseSources, serializeSources } from "@/lib/filters/sources";
import { resolvePageGrain, resolvePageRange } from "@/lib/filters/period";
import type { ColumnDef } from "@tanstack/react-table";
import type { SalesBySourceRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

// Net sales, orders, and items by Square source — reads square_transactions
// (+ item_lines) so the Source multi-select and breakdown toggle work.
// Home/Labor still use vw_model_labor_daily (no source dimension).
// Composition = bars (+ optional by-source stacks); Trend = lines (+ optional prior).
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
    mode?: string;
    compare?: string;
  }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const grain = await resolvePageGrain(sp.grain);
  const sources = parseSources(sp.sources);
  const modeRaw = parseChartMode(sp.mode);
  const coherent = assertModeFilterCoherence(
    modeRaw,
    parseBreakdown(sp.breakdown),
    parseCompare(sp.compare),
  );
  const { mode, breakdown, compare } = coherent;
  const showCustomPicker = wantsCustom(sp.range) || win.preset === "custom";
  const dateParams: Record<string, string> =
    win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const sourcesParam = serializeSources(sources);
  const breakdownParam = breakdown ? "1" : "0";
  const compareLabel =
    grain === "week" ? "Previous week" : grain === "month" ? "Previous month" : "Previous day";
  const priorSeriesLabel = compareLabel;

  let rows: SalesBySourceRow[] = [];
  let priorRows: SalesBySourceRow[] = [];
  let sourceOptions: string[] = [];
  let goalWeekly: number | undefined;
  let error: string | undefined;
  const prior = compare ? priorWindow(win, grain) : null;
  try {
    const [sales, opts, config, priorSales] = await Promise.all([
      salesByGrain(win, grain, sources, breakdown),
      salesSourceOptions(win),
      storeConfig(DEFAULT_STORE),
      prior
        ? salesByGrain(prior, grain, sources, false)
        : Promise.resolve([] as SalesBySourceRow[]),
    ]);
    rows = sales;
    priorRows = priorSales;
    sourceOptions = opts.map((r) => r.source);
    const g = config.find((r) => r.key === "goal_net_sales_weekly");
    goalWeekly = g ? Number(g.value) / 7 : undefined;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const sorted = [...rows].sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1));

  // For Trend+Compare: zero-fill both spines so bucket counts match priorWindow(grain).
  const chartSourceRows = (() => {
    if (!compare || breakdown) return sorted;
    return fillSalesSpine(sorted, enumerateBucketStarts(win, grain));
  })();
  const chartRows = chartSourceRows.map((r) => ({
    ...r,
    date: formatBucket(r.date, grain),
  }));

  let netChart = pivotSalesChart(chartRows, "net_sales", breakdown, "Net sales");
  let ordersChart = pivotSalesChart(chartRows, "orders", breakdown, "Orders");
  let itemsChart = pivotSalesChart(chartRows, "items_sold", breakdown, "Items sold");

  if (compare && prior) {
    const priorBuckets = enumerateBucketStarts(prior, grain);
    const priorFilled = fillSalesSpine(priorRows, priorBuckets, "null");
    const priorChartRows = priorFilled.map((r) => ({
      ...r,
      date: formatBucket(r.date, grain),
    }));
    const priorLabels = priorBuckets.map((iso) => formatBucket(iso, grain));
    const priorNet = pivotSalesChart(priorChartRows, "net_sales", false, "Net sales");
    const priorOrders = pivotSalesChart(priorChartRows, "orders", false, "Orders");
    const priorItems = pivotSalesChart(priorChartRows, "items_sold", false, "Items sold");
    netChart = mergePriorSeries(netChart, priorNet, "net_sales", priorSeriesLabel, priorLabels);
    ordersChart = mergePriorSeries(
      ordersChart,
      priorOrders,
      "orders",
      priorSeriesLabel,
      priorLabels,
    );
    itemsChart = mergePriorSeries(
      itemsChart,
      priorItems,
      "items_sold",
      priorSeriesLabel,
      priorLabels,
    );
  }

  const priorSubtitle =
    compare && prior
      ? `Each point vs previous ${grain} · prior data ${prior.start} → ${prior.end}`
      : undefined;

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

  const showGoal =
    mode === "composition" && grain === "day" && sources == null && !breakdown;

  const noneSelected = sources != null && sources.length === 0;
  const detailSuffix =
    sources == null
      ? ""
      : noneSelected
        ? " · no sources selected"
        : ` · ${sources.length} source${sources.length === 1 ? "" : "s"}`;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Sales"
        subtitle={`Net sales, orders, and items sold · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterPills
              label="Chart"
              param="mode"
              value={modeParam}
              options={[
                { value: "composition", label: "Composition" },
                { value: "trend", label: "Trend" },
              ]}
              basePath="/sales"
              extraParams={{
                range: win.preset,
                grain,
                ...dateParams,
                ...(sourcesParam ? { sources: sourcesParam } : {}),
                // Mode switch clears the gated control of the other mode.
              }}
            />
            <FilterMultiSelect
              label="Source"
              param="sources"
              selected={sources}
              options={sourceOptions}
              basePath="/sales"
              extraParams={{
                range: win.preset,
                grain,
                mode: modeParam,
                ...(mode === "composition"
                  ? { breakdown: breakdownParam }
                  : { compare: compareParam }),
                ...dateParams,
              }}
            />
            {mode === "composition" ? (
              <FilterPills
                label="View"
                param="breakdown"
                value={breakdownParam}
                options={[
                  { value: "0", label: "Aggregate" },
                  { value: "1", label: "By source" },
                ]}
                basePath="/sales"
                extraParams={{
                  range: win.preset,
                  grain,
                  mode: modeParam,
                  ...dateParams,
                  ...(sourcesParam ? { sources: sourcesParam } : {}),
                }}
              />
            ) : (
              <FilterPills
                label="Compare"
                param="compare"
                value={compareParam}
                options={[
                  { value: "0", label: "Off" },
                  { value: "1", label: compareLabel },
                ]}
                basePath="/sales"
                extraParams={{
                  range: win.preset,
                  grain,
                  mode: modeParam,
                  ...dateParams,
                  ...(sourcesParam ? { sources: sourcesParam } : {}),
                }}
              />
            )}
            <AggregationSelect
              value={grain}
              basePath="/sales"
              extraParams={{
                range: win.preset,
                mode: modeParam,
                ...dateParams,
                ...(sourcesParam ? { sources: sourcesParam } : {}),
                ...(mode === "composition"
                  ? { breakdown: breakdownParam }
                  : { compare: compareParam }),
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
                mode: modeParam,
                ...(sourcesParam ? { sources: sourcesParam } : {}),
                ...(mode === "composition"
                  ? { breakdown: breakdownParam }
                  : { compare: compareParam }),
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
                  mode: modeParam,
                  ...(sourcesParam ? { sources: sourcesParam } : {}),
                  ...(mode === "composition"
                    ? { breakdown: breakdownParam }
                    : { compare: compareParam }),
                }}
              />
            ) : null}
          </>
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : noneSelected ? (
        <p className="text-sm text-muted-foreground">
          No sources selected — pick one or more in Source, or Select all.
        </p>
      ) : mode === "composition" ? (
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
              {detailSuffix}
            </h2>
            <DataTable columns={columns} data={tableRows} />
          </div>
        </>
      ) : (
        <>
          <LineChartCard
            title={`Net sales by ${grain}${compare ? " vs prior" : ""}`}
            subtitle={priorSubtitle}
            data={netChart.data}
            xKey="date"
            series={netChart.series}
          />
          <LineChartCard
            title={`Orders by ${grain}${compare ? " vs prior" : ""}`}
            subtitle={priorSubtitle}
            data={ordersChart.data}
            xKey="date"
            series={ordersChart.series}
          />
          <LineChartCard
            title={`Items sold by ${grain}${compare ? " vs prior" : ""}`}
            subtitle={priorSubtitle}
            data={itemsChart.data}
            xKey="date"
            series={itemsChart.series}
          />
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              {grain === "day" ? "Daily" : grain === "week" ? "Weekly" : "Monthly"} detail
              {detailSuffix}
            </h2>
            <DataTable columns={columns} data={tableRows} />
          </div>
        </>
      )}
    </div>
  );
}
