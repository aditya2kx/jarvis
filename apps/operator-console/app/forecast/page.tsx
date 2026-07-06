import { forecastByGrain, forecastAccuracyByGrain, forecastExclusions, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterPills } from "@/components/filters/FilterPills";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, formatBucket, parseGrain, resolveRange } from "@/lib/filters/range";
import { Badge } from "@/components/ui/badge";
import type { ColumnDef } from "@tanstack/react-table";
import type { ForecastExclusionRow, ForecastRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

type Metric = "orders" | "items";

// Grafana's own hardcoded default (dashboard.json `goal_hours_per_item`
// textbox variable) — used when the operator hasn't set a `store_config`
// override for this store yet (no such key exists today; see M5 in the
// plan). Console goals are store_config-first, Grafana-default-fallback,
// same pattern as every other goal on this page's Labor/Sales siblings.
const DEFAULT_GOAL_HOURS_PER_ITEM = 0.2;

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
  searchParams: Promise<{ range?: string; metric?: string; from?: string; to?: string; grain?: string }>;
}) {
  const sp = await searchParams;
  // Forecast mixes a forward-looking "upcoming schedule" (empty on a
  // past-only preset, since forecast rows only exist from the pipeline's
  // run date forward) with a backward-looking accuracy view — same 7
  // presets as every other Performance screen, just defaulted to This
  // month since that's the more useful default for a schedule view.
  const win = resolveRange(sp.range, "this_month", sp.from, sp.to);
  const grain = parseGrain(sp.grain);
  const dateParams: Record<string, string> = win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const metric = parseMetric(sp.metric);
  const forecastKey = metric === "orders" ? "forecast_orders" : "forecast_items";
  const priorKey = metric === "orders" ? "prior_wk_orders" : "prior_wk_items";
  const actualKey = metric === "orders" ? "actual_orders" : "actual_items";

  let rows: ForecastRow[] = [];
  let accuracyChart: Record<string, unknown>[] = [];
  let exclusions: ForecastExclusionRow[] = [];
  let mapePct: number | undefined;
  let goalHoursPerItem = DEFAULT_GOAL_HOURS_PER_ITEM;
  let error: string | undefined;
  try {
    const [fc, acc, excl, config] = await Promise.all([
      forecastByGrain(win, grain),
      forecastAccuracyByGrain(win, grain),
      forecastExclusions(),
      storeConfig(DEFAULT_STORE),
    ]);
    rows = fc;
    exclusions = excl;
    const goalRow = config.find((r) => r.key === "goal_hours_per_item");
    if (goalRow) goalHoursPerItem = Number(goalRow.value);
    accuracyChart = [...acc]
      .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
      .map((r) => ({
        date: formatBucket(r.date, grain),
        forecast: r[forecastKey] as number,
        actual: r[actualKey] as number,
      }));
    mapePct = mape(acc.map((r) => ({ forecast: r[forecastKey] as number, actual: r[actualKey] as number })));
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  // Goal Total Hours vs Scheduled Part Time (Grafana panels 71/id-2230) —
  // computed client-side from already-fetched forecast rows (forecast_items
  // × goal hrs/item), never a new BQ column, same "presentation math over
  // fetched rows" pattern lib/kpi/health.ts uses for Home's pace/status.
  const scheduleRows = rows.map((r) => {
    const goalShiftHours = Number((r.forecast_items * goalHoursPerItem).toFixed(1));
    const schedVsGoalHours =
      r.scheduled_hours != null ? Number((r.scheduled_hours - goalShiftHours).toFixed(1)) : null;
    const schedVsGoalPct =
      r.scheduled_hours != null && goalShiftHours
        ? (r.scheduled_hours - goalShiftHours) / goalShiftHours
        : null;
    return { ...r, goal_shift_hours: goalShiftHours, sched_vs_goal_hours: schedVsGoalHours, sched_vs_goal_pct: schedVsGoalPct };
  });

  const scheduleChart = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatBucket(r.date, grain),
      forecast: r[forecastKey] as number,
      prior_wk: r[priorKey] as number,
    }));

  const goalHoursChart = [...scheduleRows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatBucket(r.date, grain),
      goal_shift_hours: r.goal_shift_hours,
      scheduled_hours: r.scheduled_hours,
    }));

  const metricLabel = metric === "orders" ? "orders" : "items";

  const columns: ColumnDef<(typeof scheduleRows)[number]>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "bucket", grain } } },
    // "Day" only means anything at day grain — a week/month bucket spans
    // multiple days of week, so the column is omitted rather than shown
    // blank (dow is queried as NULL for those grains — see forecastByGrain).
    ...(grain === "day" ? [{ accessorKey: "dow", header: "Day" } as ColumnDef<(typeof scheduleRows)[number]>] : []),
    { accessorKey: forecastKey, header: `Fcst ${metricLabel}`, meta: { format: { kind: "number" } } },
    { accessorKey: priorKey, header: "Prior wk", meta: { format: { kind: "number" } } },
    {
      accessorKey: metric === "orders" ? "orders_vs_prior_wk" : "items_vs_prior_wk",
      header: "vs prior wk",
      meta: { format: { kind: "pct", digits: 1 } },
    },
    { accessorKey: "scheduled_hours", header: "Scheduled hrs", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "goal_shift_hours", header: "Goal hrs", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "sched_vs_goal_hours",
      header: "Sched − Goal (hrs)",
      meta: { format: { kind: "number", digits: 1, thresholds: { warn: -2, bad: -5, direction: "lower-bad" } } },
    },
    {
      accessorKey: "sched_vs_goal_pct",
      header: "Sched vs Goal",
      meta: { format: { kind: "pct", digits: 1, thresholds: { warn: -0.1, bad: -0.25, direction: "lower-bad" } } },
    },
  ];

  const exclusionColumns: ColumnDef<ForecastExclusionRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "dow", header: "Day" },
    { accessorKey: "orders", header: "Orders", meta: { format: { kind: "number" } } },
    { accessorKey: "orders_vs_prev_wk", header: "Orders vs prior wk", meta: { format: { kind: "pct", digits: 1 } } },
    { accessorKey: "items_sold", header: "Items", meta: { format: { kind: "number" } } },
    { accessorKey: "items_vs_prev_wk", header: "Items vs prior wk", meta: { format: { kind: "pct", digits: 1 } } },
    { accessorKey: "net_sales", header: "Net sales", meta: { format: { kind: "dollars" } } },
    { accessorKey: "net_sales_vs_prev_wk", header: "Net sales vs prior wk", meta: { format: { kind: "pct", digits: 1 } } },
    { accessorKey: "aov", header: "AOV", meta: { format: { kind: "dollars" } } },
    { accessorKey: "excluded_status", header: "Excluded?", meta: { format: { kind: "status" } } },
    { accessorKey: "outlier_reason", header: "Outlier reason" },
    { accessorKey: "forecast_exclude_reason", header: "Exclude reason" },
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
              extraParams={{ range: win.preset, grain, ...dateParams }}
            />
            <AggregationSelect
              value={grain}
              basePath="/forecast"
              extraParams={{ range: win.preset, metric, ...dateParams }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={win.preset}
              options={RANGE_PRESETS}
              basePath="/forecast"
              extraParams={{ metric, grain }}
            />
            {win.preset === "custom" ? (
              <DateRangePicker basePath="/forecast" from={win.start} to={win.end} extraParams={{ metric, grain }} />
            ) : null}
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
            title={`Forecast accuracy (${metricLabel}) — ${win.label.toLowerCase()}`}
            data={accuracyChart}
            xKey="date"
            series={[
              { key: "forecast", label: `Forecast ${metricLabel}` },
              { key: "actual", label: `Actual ${metricLabel}` },
            ]}
          />
          <LineChartCard
            title={`Goal total hours vs scheduled (${goalHoursPerItem.toFixed(2)} hrs/item goal)`}
            data={goalHoursChart}
            xKey="date"
            series={[
              { key: "goal_shift_hours", label: "Goal total hours" },
              { key: "scheduled_hours", label: "Scheduled part time" },
            ]}
          />
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Upcoming schedule</h2>
            {scheduleRows.length ? (
              <DataTable columns={columns} data={scheduleRows} />
            ) : (
              <p className="text-sm text-muted-foreground">
                No forecast rows for {win.label.toLowerCase()} — this preset is entirely in the
                past, and forecast rows only exist from the pipeline&apos;s run date forward. Try
                &quot;This month&quot; or &quot;This week&quot; to see the upcoming schedule.
              </p>
            )}
          </div>
          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Forecast inputs & exclusions — last 60 days
            </h2>
            <p className="mb-2 text-xs text-muted-foreground">
              Read-only. Overriding <code>forecast_exclude</code> for a day is a BQ-tab edit (same
              as Grafana) — not exposed here.
            </p>
            <DataTable columns={exclusionColumns} data={exclusions} pinLeft={["date"]} />
          </div>
        </>
      )}
    </div>
  );
}
