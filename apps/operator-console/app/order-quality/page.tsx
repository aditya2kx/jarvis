import { kdsBySource, kdsOrderInvestigation, orderQualityByGrain } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatDate } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterPills } from "@/components/filters/FilterPills";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, formatBucket, parseGrain, resolveRange, wantsCustom } from "@/lib/filters/range";
import type { ColumnDef } from "@tanstack/react-table";
import type { KdsOrderInvestigationRow, OrderQualityDailyRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

const ON_TIME_OPTIONS = [5, 7, 10];
const MIN_PER_ITEM_OPTIONS = [5, 8, 10];

function parseOnTime(value: string | string[] | undefined): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return ON_TIME_OPTIONS.includes(n) ? n : 7;
}

function parseMinPerItem(value: string | string[] | undefined): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return MIN_PER_ITEM_OPTIONS.includes(n) ? n : 8; // Grafana panel 52 default
}

function parseSource(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "All";
}

// Chart tooltips/goal-line values are minutes — 1 decimal reads as "6.8m",
// not the raw "6.833333333333333" the BQ FLOAT64 column carries.
function round1(n: number | null | undefined): number | null | undefined {
  return n == null ? n : Number(n.toFixed(1));
}

export default async function OrderQualityPage({
  searchParams,
}: {
  searchParams: Promise<{
    range?: string;
    onTime?: string;
    source?: string;
    minPerItem?: string;
    from?: string;
    to?: string;
    grain?: string;
  }>;
}) {
  const sp = await searchParams;
  const win = resolveRange(sp.range, "30d", sp.from, sp.to);
  const grain = parseGrain(sp.grain);
  const showCustomPicker = wantsCustom(sp.range);
  const dateParams: Record<string, string> = win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const onTime = parseOnTime(sp.onTime);
  const source = parseSource(sp.source);
  const minPerItem = parseMinPerItem(sp.minPerItem);

  let rows: OrderQualityDailyRow[] = [];
  let investigationRows: KdsOrderInvestigationRow[] = [];
  let bySourceChart: Record<string, unknown>[] = [];
  let sourceOptions: string[] = [];
  let error: string | undefined;
  try {
    const [oq, src, investigation] = await Promise.all([
      orderQualityByGrain(win, grain, source, onTime),
      kdsBySource(win),
      kdsOrderInvestigation(win, source, minPerItem),
    ]);
    rows = oq;
    investigationRows = investigation;

    sourceOptions = Array.from(new Set(src.map((r) => r.order_source))).sort();
    const filteredSrc = source === "All" ? src : src.filter((r) => r.order_source === source);

    // Pivot per-source rows into one chart series per order_source. Always
    // day-grain — a per-source daily breakdown, kept as-is per M3 scope
    // ("keep the p95-by-source chart"); the grain control governs the
    // percentile chart/table above, not this one.
    const bySourceDate = new Map<string, Record<string, unknown>>();
    for (const r of filteredSrc) {
      const key = formatDate(r.date);
      const entry = bySourceDate.get(key) ?? { date: key };
      entry[r.order_source] = round1(r.kds_p95_min);
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
      kds_median_min: round1(r.kds_median_min),
    }));

  const sourceKeys =
    source === "All"
      ? Array.from(new Set(bySourceChart.flatMap((r) => Object.keys(r).filter((k) => k !== "date"))))
      : [source];

  // Thresholds are page-driven (not baked into DataTable) since the "on-time"
  // goal is an operator-chosen filter, not a fixed constant — see M2's
  // On-time pill. % items over goal has no per-store goal column yet, so a
  // fixed 5%/15% band is used (mirrors Figma's amber/red bands).
  //
  // `% tickets late` is dropped here: it's a vw_order_quality_daily-only
  // column with no multi-grain-safe definition (it was pre-computed at day
  // grain from a different source table than `per_item_min`'s
  // vw_kds_per_item_min) — see EXECUTION.md's grain-limitation note.
  const columns: ColumnDef<OrderQualityDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "bucket", grain } } },
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

  // Grafana panel 52 parity — one row per order (ticket), slowest first.
  // Date/Order pinned so they stay visible while scrolling the remaining
  // columns on narrow/mobile viewports (same convention as the M3 dual-date
  // reco table's `pinLeft`).
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

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Order Quality"
        subtitle={`KDS prep time and on-time performance · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterPills
              label="On-time"
              param="onTime"
              value={String(onTime)}
              options={ON_TIME_OPTIONS.map((m) => ({ value: String(m), label: `${m}m` }))}
              basePath="/order-quality"
              extraParams={{ range: win.preset, source, grain, minPerItem: String(minPerItem), ...dateParams }}
            />
            <FilterSelect
              label="Source"
              param="source"
              value={source}
              options={[
                { value: "All", label: "All" },
                ...sourceOptions.map((s) => ({ value: s, label: s })),
              ]}
              basePath="/order-quality"
              extraParams={{ range: win.preset, onTime: String(onTime), grain, minPerItem: String(minPerItem), ...dateParams }}
            />
            <AggregationSelect
              value={grain}
              basePath="/order-quality"
              extraParams={{ range: win.preset, onTime: String(onTime), source, minPerItem: String(minPerItem), ...dateParams }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/order-quality"
              extraParams={{ onTime: String(onTime), source, grain, minPerItem: String(minPerItem) }}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/order-quality"
                from={win.start}
                to={win.end}
                extraParams={{ onTime: String(onTime), source, grain, minPerItem: String(minPerItem) }}
              />
            ) : null}
          </>
        }
      />

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
            goal={onTime}
            goalLabel={`On-time goal (${onTime}m)`}
          />
          {sourceKeys.length ? (
            <LineChartCard
              title={source === "All" ? "KDS p95 by order source" : `KDS p95 — ${source}`}
              data={bySourceChart}
              xKey="date"
              series={sourceKeys.map((k) => ({ key: k, label: k }))}
              goal={onTime}
              goalLabel={`On-time goal (${onTime}m)`}
            />
          ) : null}
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              {grain === "day" ? "Daily" : grain === "week" ? "Weekly" : "Monthly"} percentile detail
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
                extraParams={{ range: win.preset, onTime: String(onTime), source, grain, ...dateParams }}
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
                {source !== "All" ? ` · ${source}` : ""}.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
