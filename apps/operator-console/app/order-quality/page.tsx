import { kdsBySource, orderQualityDaily } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatDate } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterPills } from "@/components/filters/FilterPills";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { RANGE_PRESETS, resolveRange } from "@/lib/filters/range";
import type { ColumnDef } from "@tanstack/react-table";
import type { OrderQualityDailyRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

const ON_TIME_OPTIONS = [5, 7, 10];

function parseOnTime(value: string | string[] | undefined): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return ON_TIME_OPTIONS.includes(n) ? n : 7;
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
  searchParams: Promise<{ range?: string; onTime?: string; source?: string }>;
}) {
  const sp = await searchParams;
  const win = resolveRange(sp.range, "30d");
  const onTime = parseOnTime(sp.onTime);
  const source = parseSource(sp.source);

  let rows: OrderQualityDailyRow[] = [];
  let bySourceChart: Record<string, unknown>[] = [];
  let sourceOptions: string[] = [];
  let error: string | undefined;
  try {
    const [oq, src] = await Promise.all([orderQualityDaily(win), kdsBySource(win)]);
    rows = oq;

    sourceOptions = Array.from(new Set(src.map((r) => r.order_source))).sort();
    const filteredSrc = source === "All" ? src : src.filter((r) => r.order_source === source);

    // Pivot per-source rows into one chart series per order_source.
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
      date: formatDate(r.date),
      kds_p95_min: round1(r.kds_p95_min),
      kds_median_min: round1(r.kds_median_min),
    }));

  const sourceKeys =
    source === "All"
      ? Array.from(new Set(bySourceChart.flatMap((r) => Object.keys(r).filter((k) => k !== "date"))))
      : [source];

  // Thresholds are page-driven (not baked into DataTable) since the "on-time"
  // goal is an operator-chosen filter, not a fixed constant — see M2's
  // On-time pill. % late / % over goal have no per-store goal column yet, so
  // fixed 5%/15% bands are used (mirrors Figma's amber/red bands).
  const columns: ColumnDef<OrderQualityDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "kds_median_min", header: "Median (min)", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "kds_p90_min", header: "p90 (min)", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "kds_p95_min",
      header: "p95 (min)",
      meta: { format: { kind: "number", digits: 1, thresholds: { warn: onTime, bad: onTime + 3, direction: "higher-bad" } } },
    },
    { accessorKey: "kds_p99_min", header: "p99 (min)", meta: { format: { kind: "number", digits: 1 } } },
    {
      accessorKey: "kds_pct_tickets_late",
      header: "% tickets late",
      meta: { format: { kind: "pct", thresholds: { warn: 0.05, bad: 0.15, direction: "higher-bad" } } },
    },
    {
      accessorKey: "kds_pct_items_over_goal",
      header: "% items over goal",
      meta: { format: { kind: "pct", thresholds: { warn: 0.05, bad: 0.15, direction: "higher-bad" } } },
    },
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
              extraParams={{ range: win.preset, source }}
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
              extraParams={{ range: win.preset, onTime: String(onTime) }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={win.preset}
              options={RANGE_PRESETS}
              basePath="/order-quality"
              extraParams={{ onTime: String(onTime), source }}
            />
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
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">Daily percentile detail</h2>
            <DataTable columns={columns} data={rows} />
          </div>
        </>
      )}
    </div>
  );
}
