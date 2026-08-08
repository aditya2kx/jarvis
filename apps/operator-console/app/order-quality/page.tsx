import { kdsBySource, kdsOrderInvestigation, orderQualityByGrain } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatDate } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterPills } from "@/components/filters/FilterPills";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterMultiSelect } from "@/components/filters/FilterMultiSelect";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, formatBucket, grainTitleLabel, wantsCustom } from "@/lib/filters/range";
import { resolvePageGrain, resolvePageRange } from "@/lib/filters/period";
import { parseBreakdown, parseSources, serializeSources } from "@/lib/filters/sources";
import {
  buildOqAggregateSeries,
  buildOqBySourceSeries,
  oqMetricField,
  oqMetricLabel,
  parseOqMetric,
} from "@/lib/charts/order-quality";
import type { ColumnDef } from "@tanstack/react-table";
import type { KdsOrderInvestigationRow, OrderQualityDailyRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

/** On-time goal minutes — default 8m (store prep goal). */
const ON_TIME_OPTIONS = [5, 8, 10];
const MIN_PER_ITEM_OPTIONS = [5, 8, 10];

function parseOnTime(value: string | string[] | undefined): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return ON_TIME_OPTIONS.includes(n) ? n : 8;
}

function parseMinPerItem(value: string | string[] | undefined): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return MIN_PER_ITEM_OPTIONS.includes(n) ? n : 8;
}

function round1(n: number | null | undefined): number | null | undefined {
  return n == null ? n : Number(n.toFixed(1));
}

export default async function OrderQualityPage({
  searchParams,
}: {
  searchParams: Promise<{
    range?: string;
    onTime?: string;
    sources?: string;
    /** @deprecated single-select; still accepted and mapped into `sources`. */
    source?: string;
    minPerItem?: string;
    from?: string;
    to?: string;
    grain?: string;
    metric?: string;
    breakdown?: string;
  }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const grainRaw = await resolvePageGrain(sp.grain);
  const grain = grainRaw === "hour" ? "day" : grainRaw;
  const showCustomPicker = wantsCustom(sp.range) || win.preset === "custom";
  const dateParams: Record<string, string> = win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const onTime = parseOnTime(sp.onTime);
  // Prefer multi `sources=`; legacy `source=` (single / All) still works.
  const sources =
    sp.sources !== undefined
      ? parseSources(sp.sources)
      : sp.source && sp.source !== "All"
        ? [String(Array.isArray(sp.source) ? sp.source[0] : sp.source)]
        : null;
  const sourcesParam = serializeSources(sources);
  const minPerItem = parseMinPerItem(sp.minPerItem);
  const metric = parseOqMetric(sp.metric);
  const breakdown = parseBreakdown(sp.breakdown);
  const metricParam = metric;
  const breakdownParam = breakdown ? "1" : "0";
  const noneSelected = sources != null && sources.length === 0;

  let rows: OrderQualityDailyRow[] = [];
  let investigationRows: KdsOrderInvestigationRow[] = [];
  let bySourceChart: Record<string, unknown>[] = [];
  let sourceOptions: string[] = [];
  let error: string | undefined;
  try {
    const [oq, src, investigation] = await Promise.all([
      noneSelected
        ? Promise.resolve([] as OrderQualityDailyRow[])
        : orderQualityByGrain(win, grain, sources, onTime),
      kdsBySource(win),
      noneSelected
        ? Promise.resolve([] as KdsOrderInvestigationRow[])
        : kdsOrderInvestigation(win, sources, minPerItem),
    ]);
    rows = oq;
    investigationRows = investigation;

    sourceOptions = Array.from(new Set(src.map((r) => r.order_source))).sort();
    const filteredSrc =
      sources == null
        ? src
        : src.filter((r) => sources.includes(r.order_source));
    const field = oqMetricField(metric);

    const bySourceDate = new Map<string, Record<string, unknown>>();
    for (const r of filteredSrc) {
      const key = formatDate(r.date);
      const entry = bySourceDate.get(key) ?? { date: key };
      entry[r.order_source] = round1(Number(r[field]));
      bySourceDate.set(key, entry);
    }
    bySourceChart = Array.from(bySourceDate.values());
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const chartData = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatBucket(r.date, grain),
      kds_p95_min: round1(r.kds_p95_min),
      kds_avg_min: round1(r.kds_avg_min),
    }));

  const sourceKeys = Array.from(
    new Set(bySourceChart.flatMap((r) => Object.keys(r).filter((k) => k !== "date"))),
  );

  const chartTitle = breakdown
    ? sources == null
      ? `KDS ${oqMetricLabel(metric)} by order source`
      : noneSelected
        ? `KDS ${oqMetricLabel(metric)} — no sources`
        : `KDS ${oqMetricLabel(metric)} — ${sources.length} source${sources.length === 1 ? "" : "s"}`
    : `KDS prep time (${oqMetricLabel(metric)})`;

  const chartSeries = breakdown
    ? buildOqBySourceSeries(sourceKeys)
    : buildOqAggregateSeries(metric);

  const chartRows = breakdown ? bySourceChart : chartData;

  const columns: ColumnDef<OrderQualityDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "bucket", grain } } },
    { accessorKey: "kds_avg_min", header: "Average (min)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "kds_median_min", header: "Median (min)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "kds_p90_min", header: "p90 (min)", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "kds_p95_min",
      header: "p95 (min)",
      meta: { format: { kind: "number", digits: 1, thresholds: { warn: onTime, bad: onTime + 3, direction: "higher-bad" } } },
    },
    { accessorKey: "kds_p99_min", header: "p99 (min)", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "kds_pct_items_over_goal",
      header: "% items over goal",
      meta: { format: { kind: "pct", thresholds: { warn: 0.05, bad: 0.15, direction: "higher-bad" } } },
    },
  ];

  const investigationColumns: ColumnDef<KdsOrderInvestigationRow>[] = [
    { accessorKey: "date_local", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "ticket_name", header: "Order" },
    { accessorKey: "order_source", header: "Source" },
    { accessorKey: "start_time", header: "Start" },
    { accessorKey: "end_time", header: "End" },
    { accessorKey: "num_items", header: "Items", meta: { format: { kind: "number" } } },
    { accessorKey: "order_min", header: "Order Min", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "min_per_item",
      header: "Min / Item",
      meta: { format: { kind: "number", digits: 1, thresholds: { warn: minPerItem, bad: minPerItem + 3, direction: "higher-bad" } } },
    },
    { accessorKey: "staff_on_shift", header: "On Shift (punched in)" },
    { accessorKey: "items_in_ticket", header: "Items in Order" },
  ];

  const sourcesExtra = sourcesParam ? { sources: sourcesParam } : {};
  const sharedExtra = {
    range: win.preset,
    onTime: String(onTime),
    grain,
    minPerItem: String(minPerItem),
    metric: metricParam,
    breakdown: breakdownParam,
    ...sourcesExtra,
    ...dateParams,
  };

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Order Quality"
        subtitle={`KDS prep time and on-time performance · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterPills
              label="Metric"
              param="metric"
              value={metricParam}
              options={[
                { value: "p95", label: "P95" },
                { value: "avg", label: "Average" },
              ]}
              basePath="/order-quality"
              extraParams={{
                range: win.preset,
                onTime: String(onTime),
                grain,
                minPerItem: String(minPerItem),
                breakdown: breakdownParam,
                ...sourcesExtra,
                ...dateParams,
              }}
            />
            <FilterPills
              label="View"
              param="breakdown"
              value={breakdownParam}
              options={[
                { value: "0", label: "Aggregate" },
                { value: "1", label: "By-source" },
              ]}
              basePath="/order-quality"
              extraParams={{
                range: win.preset,
                onTime: String(onTime),
                grain,
                minPerItem: String(minPerItem),
                metric: metricParam,
                ...sourcesExtra,
                ...dateParams,
              }}
            />
            <FilterPills
              label="On-time"
              param="onTime"
              value={String(onTime)}
              options={ON_TIME_OPTIONS.map((m) => ({ value: String(m), label: `${m}m` }))}
              basePath="/order-quality"
              extraParams={{
                range: win.preset,
                grain,
                minPerItem: String(minPerItem),
                metric: metricParam,
                breakdown: breakdownParam,
                ...sourcesExtra,
                ...dateParams,
              }}
            />
            <FilterMultiSelect
              label="Source"
              param="sources"
              selected={sources}
              options={sourceOptions}
              basePath="/order-quality"
              extraParams={{
                range: win.preset,
                onTime: String(onTime),
                grain,
                minPerItem: String(minPerItem),
                metric: metricParam,
                breakdown: breakdownParam,
                ...dateParams,
              }}
            />
            <AggregationSelect
              value={grain}
              basePath="/order-quality"
              extraParams={{
                range: win.preset,
                onTime: String(onTime),
                minPerItem: String(minPerItem),
                metric: metricParam,
                breakdown: breakdownParam,
                ...sourcesExtra,
                ...dateParams,
              }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/order-quality"
              extraParams={{
                onTime: String(onTime),
                grain,
                minPerItem: String(minPerItem),
                metric: metricParam,
                breakdown: breakdownParam,
                ...sourcesExtra,
              }}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/order-quality"
                from={win.start}
                to={win.end}
                committed={win.preset === "custom"}
                extraParams={{
                  onTime: String(onTime),
                  grain,
                  minPerItem: String(minPerItem),
                  metric: metricParam,
                  breakdown: breakdownParam,
                  ...sourcesExtra,
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
      ) : (
        <>
          {chartSeries.length && chartRows.length ? (
            <BarChartCard
              title={chartTitle}
              data={chartRows}
              xKey="date"
              series={chartSeries}
              stacked={false}
              valueFormat="number"
              goal={onTime}
              goalLabel={`On-time goal (${onTime}m)`}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              No KDS prep-time points for {win.label.toLowerCase()}
              {sources != null ? ` · ${sources.length} source${sources.length === 1 ? "" : "s"}` : ""}.
            </p>
          )}
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              {grainTitleLabel(grain)} percentile detail
            </h2>
            <DataTable columns={columns} data={rows} />
          </div>
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-medium text-muted-foreground">
                Order KDS times (slowest first)
              </h2>
              <FilterPills
                label="Min / Item ≥"
                param="minPerItem"
                value={String(minPerItem)}
                options={MIN_PER_ITEM_OPTIONS.map((m) => ({ value: String(m), label: `${m}m` }))}
                basePath="/order-quality"
                extraParams={sharedExtra}
              />
            </div>
            <p className="mb-2 text-xs text-muted-foreground">
              One row per order (ticket), sorted by Min/Item = Order Min ÷ Items. &quot;On Shift&quot;
              lists everyone whose ADP punch overlapped that order&apos;s time window.
            </p>
            {investigationRows.length ? (
              <DataTable
                columns={investigationColumns}
                data={investigationRows}
                pinLeft={["date_local", "ticket_name"]}
              />
            ) : (
              <p className="text-sm text-muted-foreground">
                No orders at or above {minPerItem}m/item for {win.label.toLowerCase()}
                {sources != null && sources.length
                  ? ` · ${sources.length} source${sources.length === 1 ? "" : "s"}`
                  : ""}
                .
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
