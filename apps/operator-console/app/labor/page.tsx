import { laborByGrain, laborForwardSummary, laborProjectedByDay, scheduledHoursPerPerson, storeConfig, payrollPeriod } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { LineChartCard } from "@/components/charts/LineChartCard";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterPills } from "@/components/filters/FilterPills";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { LaborForwardSummaryCard } from "@/components/kpi/LaborForwardSummary";
import { RANGE_PRESETS, formatBucket, parseGrain, wantsCustom } from "@/lib/filters/range";
import { resolvePageRange } from "@/lib/filters/period";
import { LABOR_LENS_OPTIONS, parseLaborLens, periodDayCount } from "@/lib/kpi/labor-lens";
import type { ColumnDef } from "@tanstack/react-table";
import type { LaborDailyRow, LaborForwardSummary } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

function goalFromConfig(rows: { key: string; value: string }[], key: string): number | undefined {
  const row = rows.find((r) => r.key === key);
  return row ? Number(row.value) : undefined;
}

export default async function LaborPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; from?: string; to?: string; grain?: string; lens?: string }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const grain = parseGrain(sp.grain);
  const lens = parseLaborLens(sp.lens);
  const showCustomPicker = wantsCustom(sp.range);
  const dateParams: Record<string, string> = win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const periodDays = periodDayCount(win.start, win.end);

  let rows: LaborDailyRow[] = [];
  let goalLaborPct: number | undefined;
  let hoursPerPerson: { employee: string; hours: number }[] = [];
  let scheduledPerPerson: { employee: string; hours: number; cost: number | null }[] = [];
  let forward: LaborForwardSummary | undefined;
  let projectedByDay: { date: string; projected_pt_pct: number | null }[] = [];
  let burdenPct = 0;
  let error: string | undefined;
  try {
    const [labor, config, period, fwd, schedPeople, projDays] = await Promise.all([
      laborByGrain(win, grain),
      storeConfig(DEFAULT_STORE),
      payrollPeriod(1),
      laborForwardSummary(win, DEFAULT_STORE),
      scheduledHoursPerPerson(win).catch(() => []),
      laborProjectedByDay(win).catch(() => []),
    ]);
    rows = labor;
    forward = fwd;
    scheduledPerPerson = schedPeople;
    projectedByDay = projDays;
    burdenPct = fwd.laborBurdenPct > 0 ? fwd.laborBurdenPct : 0;
    goalLaborPct = goalFromConfig(config, "goal_labor_pct_max");
    const openPeriod = period.find((p) => p.is_open) ?? period[0];
    hoursPerPerson = period
      .filter((p) => p.period_start === openPeriod?.period_start)
      .map((p) => ({ employee: p.employee, hours: p.hours_worked }))
      .sort((a, b) => b.hours - a.hours);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const projMap = new Map(
    projectedByDay.map((p) => [
      formatBucket(p.date, grain),
      p.projected_pt_pct != null ? Number((p.projected_pt_pct * 100).toFixed(1)) : null,
    ]),
  );

  const chartData: {
    date: string;
    labor_pct: number | null;
    hourly_pct: number | null;
    fulltime_pct: number | null;
    paid_labor_pct: number | null;
    paid_hourly_pct: number | null;
    projected_pt_pct: number | null;
    hours_per_item: number | null;
    hourly_hours_per_item: number | null;
    fulltime_hours_per_item: number | null;
    total_hours: number | null;
    net_sales: number | null;
    orders_per_hour: number | null;
    items_per_hour: number | null;
    fulltime_hours: number | null;
    parttime_hours: number | null;
  }[] = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => {
      const bucket = formatBucket(r.date, grain);
      const laborPct = r.labor_pct != null ? Number((r.labor_pct * 100).toFixed(1)) : null;
      const hourlyPct = r.hourly_pct != null ? Number((r.hourly_pct * 100).toFixed(1)) : null;
      const fulltimePct = r.fulltime_pct != null ? Number((r.fulltime_pct * 100).toFixed(1)) : null;
      return {
        date: bucket,
        labor_pct: laborPct,
        hourly_pct: hourlyPct,
        fulltime_pct: fulltimePct,
        paid_labor_pct:
          laborPct != null && burdenPct > 0 ? Number((laborPct * (1 + burdenPct)).toFixed(1)) : null,
        paid_hourly_pct:
          hourlyPct != null && burdenPct > 0 ? Number((hourlyPct * (1 + burdenPct)).toFixed(1)) : null,
        projected_pt_pct: projMap.get(bucket) ?? null,
        hours_per_item: r.hours_per_item != null ? Number(r.hours_per_item) : null,
        hourly_hours_per_item:
          r.hourly_hours_per_item != null ? Number(r.hourly_hours_per_item) : null,
        fulltime_hours_per_item:
          r.fulltime_hours_per_item != null ? Number(r.fulltime_hours_per_item) : null,
        total_hours: r.total_hours != null ? Number(r.total_hours) : null,
        net_sales: r.net_sales != null ? Number(r.net_sales) : null,
        orders_per_hour: r.total_hours ? Number((r.orders / r.total_hours).toFixed(2)) : null,
        items_per_hour: r.total_hours ? Number((r.items_sold / r.total_hours).toFixed(2)) : null,
        fulltime_hours: r.fulltime_hours != null ? Number(Number(r.fulltime_hours).toFixed(1)) : null,
        parttime_hours: r.hourly_hours != null ? Number(Number(r.hourly_hours).toFixed(1)) : null,
      };
    });

  if (lens === "blended") {
    for (const [bucket, pct] of projMap) {
      if (!chartData.some((d) => d.date === bucket)) {
        chartData.push({
          date: bucket,
          labor_pct: null,
          hourly_pct: null,
          fulltime_pct: null,
          paid_labor_pct: null,
          paid_hourly_pct: null,
          projected_pt_pct: pct,
          hours_per_item: null,
          hourly_hours_per_item: null,
          fulltime_hours_per_item: null,
          total_hours: null,
          net_sales: null,
          orders_per_hour: null,
          items_per_hour: null,
          fulltime_hours: null,
          parttime_hours: null,
        });
      }
    }
  }

  const laborPctSeries =
    lens === "paid"
      ? [
          { key: "paid_labor_pct", label: "Total paid %" },
          { key: "paid_hourly_pct", label: "Part-time paid %" },
        ]
      : lens === "blended"
        ? [
            { key: "labor_pct", label: "Total wage % (completed)" },
            { key: "hourly_pct", label: "Part-time wage % (completed)" },
            { key: "projected_pt_pct", label: "Blended PT % (schedule)", dashed: true },
          ]
        : [
            { key: "labor_pct", label: "Total wage %" },
            { key: "hourly_pct", label: "Part-time wage %" },
            { key: "fulltime_pct", label: "Full-time wage %" },
          ];

  const columns: ColumnDef<LaborDailyRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "bucket", grain } } },
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
        right={
          <>
            <AggregationSelect
              value={grain}
              basePath="/labor"
              extraParams={{ range: win.preset, lens, ...dateParams }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/labor"
              extraParams={{ grain, lens }}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/labor"
                from={win.start}
                to={win.end}
                extraParams={{ grain, lens }}
              />
            ) : null}
          </>
        }
      />

      <FilterPills
        label="Lens"
        param="lens"
        value={lens}
        options={LABOR_LENS_OPTIONS}
        basePath="/labor"
        extraParams={{ range: win.preset, grain, ...dateParams }}
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          {forward ? (
            <LaborForwardSummaryCard data={forward} lens={lens} periodDays={periodDays} />
          ) : null}
          <div className="grid gap-4 md:grid-cols-2">
            <LineChartCard
              title={
                lens === "paid"
                  ? "Labor % of net sales (paid)"
                  : lens === "blended"
                    ? "Labor % of net sales (blended)"
                    : "Labor % of net sales (wage)"
              }
              data={chartData}
              xKey="date"
              series={laborPctSeries}
              goal={goalLaborPct != null ? goalLaborPct * 100 : undefined}
              goalLabel="Goal"
            />
            <LineChartCard
              title="Hours per item — total / part-time / full-time"
              data={chartData}
              xKey="date"
              series={[
                { key: "hours_per_item", label: "Total" },
                { key: "hourly_hours_per_item", label: "Part-time" },
                { key: "fulltime_hours_per_item", label: "Full-time" },
              ]}
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
            title={`Total labor hours by ${grain}`}
            data={chartData}
            xKey="date"
            series={[{ key: "total_hours", label: "Hours" }]}
          />

          {lens === "blended" ? (
            <div>
              <h2 className="mb-2 text-sm font-medium text-muted-foreground">
                Scheduled hours per person — forward (ADP)
              </h2>
              {scheduledPerPerson.length ? (
                <div className="flex flex-col divide-y divide-border rounded-md border">
                  {scheduledPerPerson.map((p) => (
                    <div
                      key={p.employee}
                      className="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                    >
                      <span>{p.employee}</span>
                      <span className="font-medium">
                        {Number(p.hours).toFixed(1)} hrs
                        {p.cost != null ? ` · $${Number(p.cost).toFixed(0)}` : ""}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No forward ADP schedule rows in this Period yet.
                </p>
              )}
            </div>
          ) : null}

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
