import {
  adpScheduleHorizonEnd,
  adpScheduleScrapedAt,
  laborActualShiftDays,
  laborByGrain,
  laborConcurrentByGrain,
  laborHoursPerPerson,
  laborScheduledHoursByGrain,
  laborScheduledShiftDays,
  storeConfig,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { LaborHoursChart } from "@/components/labor/LaborHoursChart";
import { LaborConcurrentChart } from "@/components/labor/LaborConcurrentChart";
import { LaborCoveragePanel } from "@/components/labor/LaborCoveragePanel";
import { SyncScheduledShiftsButton } from "@/components/labor/SyncScheduledShiftsButton";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterMultiSelect } from "@/components/filters/FilterMultiSelect";
import { FilterPills } from "@/components/filters/FilterPills";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import {
  RANGE_PRESETS,
  GRAINS,
  chicagoTodayIso,
  enumerateBucketStarts,
  formatBucket,
  wantsCustom,
} from "@/lib/filters/range";
import { resolvePageGrain, resolvePageRange } from "@/lib/filters/period";
import {
  LABOR_TYPE_OPTIONS,
  parseLaborTypes,
  serializeLaborTypes,
} from "@/lib/filters/labor-type";
import {
  LABOR_CHART_UNIT_OPTIONS,
  parseLaborChartUnit,
} from "@/lib/filters/labor-chart-unit";
import {
  PTO_FILTER_OPTIONS,
  parsePtoFilter,
  serializePtoFilter,
} from "@/lib/filters/pto-filter";
import {
  ROLLUP_STAT_OPTIONS,
  parseRollupStat,
  rollupStatApplicable,
} from "@/lib/filters/sales-stat";
import {
  actualPunchWindow,
  laborChartWindow,
  periodIncludesToday,
  scheduledShiftWindow,
} from "@/lib/labor/actual-schedule-windows";
import {
  aggregateScheduledDays,
  rollConcurrentToGrain,
} from "@/lib/labor/schedule-aggregate";
import type {
  LaborActualShiftDayRow,
  LaborConcurrentRow,
  LaborDailyRow,
  LaborScheduledHoursRow,
  LaborScheduledShiftDayRow,
} from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

function goalFromConfig(rows: { key: string; value: string }[], key: string): number | undefined {
  const row = rows.find((r) => r.key === key);
  return row ? Number(row.value) : undefined;
}

function isoKey(d: string | Date): string {
  return typeof d === "string" ? d.slice(0, 10) : dateSortKey(d).slice(0, 10);
}

export default async function LaborPage({
  searchParams,
}: {
  searchParams: Promise<{
    range?: string;
    from?: string;
    to?: string;
    grain?: string;
    labor_type?: string;
    pto?: string;
    day?: string;
    unit?: string;
    stat?: string;
  }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const grain = await resolvePageGrain(sp.grain);
  const showStat = rollupStatApplicable(grain);
  const stat = showStat ? parseRollupStat(sp.stat) : "total";
  const laborTypes = parseLaborTypes(sp.labor_type);
  const chartUnit = parseLaborChartUnit(sp.unit);
  const ptoFilter = parsePtoFilter(sp.pto);
  const excludePto = ptoFilter === "exclude";
  const showCustomPicker = wantsCustom(sp.range) || win.preset === "custom";
  const dateParams: Record<string, string> =
    win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const laborTypeParam = serializeLaborTypes(laborTypes);
  const laborTypeExtra: Record<string, string> = laborTypeParam
    ? { labor_type: laborTypeParam }
    : {};
  const ptoParam = serializePtoFilter(ptoFilter);
  const ptoExtra: Record<string, string> = ptoParam ? { pto: ptoParam } : {};
  const unitExtra: Record<string, string> =
    chartUnit !== "hours" ? { unit: chartUnit } : {};
  const statExtra: Record<string, string> = showStat && stat !== "avg" ? { stat } : {};
  const dayExtra: Record<string, string> = sp.day
    ? { day: sp.day.slice(0, 10) }
    : {};

  const punchWin = actualPunchWindow(win);
  const includesToday = periodIncludesToday(win);
  let chartWin = win;
  let rows: LaborDailyRow[] = [];
  let concurrentRows: LaborConcurrentRow[] = [];
  let scheduledHoursRows: LaborScheduledHoursRow[] = [];
  let scheduledConcurrentByBucket: {
    date: string;
    parttime_concurrent: number | null;
    fulltime_concurrent: number | null;
    total_concurrent: number | null;
  }[] = [];
  let goalLaborHoursWeek: number | undefined;
  let hoursPerPerson: { employee: string; hours: number }[] = [];
  let scheduleScrapedAt: string | null = null;
  let coverageActuals: LaborActualShiftDayRow[] = [];
  let coverageScheduled: LaborScheduledShiftDayRow[] = [];
  let error: string | undefined;
  try {
    // When Period includes today, extend charts through the latest ADP scheduled
    // date (any Aggregation) — not just Period end.
    const scheduleHorizonEnd = includesToday
      ? await adpScheduleHorizonEnd().catch(() => null)
      : null;
    const todayIso = chicagoTodayIso();
    chartWin = laborChartWindow(win, todayIso, scheduleHorizonEnd);
    const schedWin = scheduledShiftWindow(win, todayIso, scheduleHorizonEnd);
    // Hour grain: schedule ranges are JSON — no stacks / concurrent schedule.
    const showSchedule = includesToday && schedWin != null && grain !== "hour";

    const [
      labor,
      config,
      perPerson,
      concurrent,
      schedHours,
      schedDays,
      scraped,
      actualShiftDays,
    ] = await Promise.all([
      punchWin ? laborByGrain(punchWin, grain, stat) : Promise.resolve([]),
      storeConfig(DEFAULT_STORE),
      laborHoursPerPerson(win).catch(() => []),
      punchWin
        ? laborConcurrentByGrain(punchWin, grain, stat).catch(() => [])
        : Promise.resolve([]),
      showSchedule && schedWin
        ? laborScheduledHoursByGrain(schedWin, grain, { excludePto }).catch(() => [])
        : Promise.resolve([]),
      showSchedule && schedWin
        ? laborScheduledShiftDays(schedWin, { excludePto }).catch(() => [])
        : Promise.resolve([]),
      adpScheduleScrapedAt().catch(() => null),
      punchWin ? laborActualShiftDays(punchWin).catch(() => []) : Promise.resolve([]),
    ]);
    rows = labor;
    concurrentRows = concurrent;
    scheduledHoursRows = schedHours;
    coverageScheduled = schedDays;
    coverageActuals = actualShiftDays;
    scheduledConcurrentByBucket = rollConcurrentToGrain(
      aggregateScheduledDays(schedDays),
      grain,
    );
    scheduleScrapedAt = scraped;
    goalLaborHoursWeek = goalFromConfig(config, "goal_labor_hours_week");
    hoursPerPerson = perPerson
      .map((p) => ({ employee: p.employee, hours: Number(p.hours) || 0 }))
      .filter((p) => p.hours > 0)
      .sort((a, b) => b.hours - a.hours);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const actualByBucket = new Map(
    rows.map((r) => {
      const iso = isoKey(r.date);
      return [
        iso,
        {
          total_hours: r.total_hours != null ? Number(Number(r.total_hours).toFixed(1)) : null,
          parttime_hours: r.hourly_hours != null ? Number(Number(r.hourly_hours).toFixed(1)) : null,
          fulltime_hours:
            r.fulltime_hours != null ? Number(Number(r.fulltime_hours).toFixed(1)) : null,
          labor_pct: r.labor_pct != null ? Number(r.labor_pct) : null,
          hourly_pct: r.hourly_pct != null ? Number(r.hourly_pct) : null,
          fulltime_pct: r.fulltime_pct != null ? Number(r.fulltime_pct) : null,
          net_sales: r.net_sales != null ? Number(r.net_sales) : null,
        },
      ] as const;
    }),
  );

  const schedHoursByBucket = new Map(
    scheduledHoursRows.map((r) => {
      const iso = isoKey(r.date);
      return [
        iso,
        {
          parttime_scheduled_hours:
            r.parttime_hours != null ? Number(Number(r.parttime_hours).toFixed(1)) : null,
          fulltime_scheduled_hours:
            r.fulltime_hours != null ? Number(Number(r.fulltime_hours).toFixed(1)) : null,
        },
      ] as const;
    }),
  );

  const concurrentActualByBucket = new Map(
    concurrentRows.map((r) => {
      const iso = isoKey(r.date);
      return [
        iso,
        {
          parttime_concurrent:
            r.parttime_concurrent != null
              ? Number(Number(r.parttime_concurrent).toFixed(1))
              : null,
          fulltime_concurrent:
            r.fulltime_concurrent != null
              ? Number(Number(r.fulltime_concurrent).toFixed(1))
              : null,
          total_concurrent:
            r.total_concurrent != null ? Number(Number(r.total_concurrent).toFixed(1)) : null,
        },
      ] as const;
    }),
  );

  const concurrentSchedByBucket = new Map(
    scheduledConcurrentByBucket.map((r) => [isoKey(r.date), r] as const),
  );

  const bucketIsos = enumerateBucketStarts(chartWin, grain);
  const chartData = bucketIsos.map((iso) => {
    const a = actualByBucket.get(iso);
    const s = schedHoursByBucket.get(iso);
    return {
      date: formatBucket(iso, grain, grain === "day" ? { weekday: true } : undefined),
      bucket_iso: iso,
      total_hours: a?.total_hours ?? null,
      parttime_hours: a?.parttime_hours ?? null,
      fulltime_hours: a?.fulltime_hours ?? null,
      labor_pct: a?.labor_pct ?? null,
      hourly_pct: a?.hourly_pct ?? null,
      fulltime_pct: a?.fulltime_pct ?? null,
      net_sales: a?.net_sales ?? null,
      parttime_scheduled_hours: s?.parttime_scheduled_hours ?? null,
      fulltime_scheduled_hours: s?.fulltime_scheduled_hours ?? null,
    };
  });

  const concurrentChartData = bucketIsos.map((iso) => {
    const a = concurrentActualByBucket.get(iso);
    const s = concurrentSchedByBucket.get(iso);
    return {
      date: formatBucket(iso, grain, grain === "day" ? { weekday: true } : undefined),
      parttime_concurrent: a?.parttime_concurrent ?? null,
      fulltime_concurrent: a?.fulltime_concurrent ?? null,
      total_concurrent: a?.total_concurrent ?? null,
      parttime_scheduled_concurrent: s?.parttime_concurrent ?? null,
      fulltime_scheduled_concurrent: s?.fulltime_concurrent ?? null,
      total_scheduled_concurrent: s?.total_concurrent ?? null,
    };
  });

  const personChartData = hoursPerPerson.map((p) => ({
    employee: p.employee,
    hours: Number(p.hours.toFixed(1)),
  }));

  const statPrefix = showStat && stat === "avg" ? "Average " : showStat ? "Total " : "";
  const statSubtitle =
    showStat && stat === "avg"
      ? grain === "hour"
        ? "Per day in Period"
        : "Per weekday in Period"
      : showStat
        ? "Sum across Period"
        : undefined;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Labor"
        subtitle={`Historical ADP hours · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterPills
              label="Hours chart"
              param="unit"
              value={chartUnit}
              options={LABOR_CHART_UNIT_OPTIONS}
              basePath="/labor"
              extraParams={{
                range: win.preset,
                grain,
                ...statExtra,
                ...laborTypeExtra,
                ...ptoExtra,
                ...dateParams,
                ...dayExtra,
              }}
            />
            <AggregationSelect
              value={grain}
              basePath="/labor"
              options={GRAINS}
              extraParams={{
                range: win.preset,
                ...statExtra,
                ...laborTypeExtra,
                ...ptoExtra,
                ...unitExtra,
                ...dateParams,
                ...dayExtra,
              }}
            />
            {showStat ? (
              <FilterPills
                label="Stat"
                param="stat"
                value={stat}
                options={ROLLUP_STAT_OPTIONS}
                basePath="/labor"
                extraParams={{
                  range: win.preset,
                  grain,
                  ...laborTypeExtra,
                  ...ptoExtra,
                  ...unitExtra,
                  ...dateParams,
                  ...dayExtra,
                }}
              />
            ) : null}
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/labor"
              extraParams={{
                grain,
                ...statExtra,
                ...laborTypeExtra,
                ...ptoExtra,
                ...unitExtra,
                ...dayExtra,
              }}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/labor"
                from={win.start}
                to={win.end}
                committed={win.preset === "custom"}
                extraParams={{
                  grain,
                  ...statExtra,
                  ...laborTypeExtra,
                  ...ptoExtra,
                  ...unitExtra,
                  ...dayExtra,
                }}
              />
            ) : null}
            <FilterMultiSelect
              label="Labor type"
              param="labor_type"
              selected={laborTypes}
              options={[...LABOR_TYPE_OPTIONS]}
              basePath="/labor"
              extraParams={{
                range: win.preset,
                grain,
                ...statExtra,
                ...dateParams,
                ...ptoExtra,
                ...unitExtra,
                ...dayExtra,
              }}
            />
            <FilterSelect
              label="PTO"
              param="pto"
              value={ptoFilter}
              options={[...PTO_FILTER_OPTIONS]}
              basePath="/labor"
              extraParams={{
                range: win.preset,
                grain,
                ...statExtra,
                ...dateParams,
                ...laborTypeExtra,
                ...unitExtra,
                ...dayExtra,
              }}
            />
            <SyncScheduledShiftsButton lastScrapedAt={scheduleScrapedAt} />
          </>
        }
      />

      <div
        role="note"
        className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">Actual</span> (solid colors) =
          ADP clocked hours through yesterday.{" "}
          <span className="font-medium text-foreground">Scheduled</span> (slate) stacks
          on the hours / concurrent charts from today through the latest ADP scheduled
          dates when the Period includes today (not only through Period end) — hover
          also shows{" "}
          <span className="font-medium text-foreground">Total (combined)</span> vs
          weekly Goal.{" "}
          <span className="font-medium text-foreground">% of net sales</span> on the
          Hours chart uses completed days only (no schedule stacks).{" "}
          {grain === "hour"
            ? "Hour of day allocates clocked shifts across clock hours and pairs with Sales ops-hour net sales for %; schedule stacks are hidden. "
            : ""}
          {showStat
            ? `Stat Average = typical ${grain === "hour" ? "hour across days" : "weekday"} in the Period; Total = sum across the Period. `
            : ""}
          {goalLaborHoursWeek != null && !Number.isNaN(Number(goalLaborHoursWeek))
            ? `Weekly Goal (${Number(goalLaborHoursWeek)} hrs) is the gold dashed line on Aggregation=Weekly in Hours mode. `
            : ""}
          <span className="font-medium text-foreground">Avg concurrent</span> bars =
          actual only (schedule stays in the hover). PT/FT concurrent is hours ÷ that
          bucket&apos;s first→last span (one full-timer ≈ 1), not diluted by store-open
          hours from the other bucket.{" "}
          <span className="font-medium text-foreground">Staffing coverage</span> is a
          day strip for the Period (extended through scheduled shifts when today is
          included) with a headcount ribbon and person swimlanes — scroll the chips when
          the range is long. Use{" "}
          <span className="font-medium text-foreground">Sync scheduled shifts</span> after
          editing the ADP schedule — status under the button shows starting / syncing /
          done / error without blocking the rest of the page. Paid PTO is included in
          scheduled hours by default (matches ADP); use the PTO filter to exclude it.
          Per-person hours below sum clocked ADP hours over the Period.
        </p>
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <LaborHoursChart
            data={chartData}
            laborTypes={laborTypes}
            grain={grain}
            goalLaborHoursWeek={goalLaborHoursWeek}
            unit={chartUnit}
            titlePrefix={statPrefix}
            subtitle={statSubtitle}
          />

          <LaborConcurrentChart
            data={concurrentChartData}
            laborTypes={laborTypes}
            grain={grain}
            titlePrefix={statPrefix}
            subtitle={statSubtitle}
          />

          <LaborCoveragePanel
            win={chartWin}
            actuals={coverageActuals}
            scheduled={coverageScheduled}
            laborTypes={laborTypes}
            selectedDay={sp.day}
            basePath="/labor"
            extraParams={{
              range: win.preset,
              grain,
              ...statExtra,
              ...laborTypeExtra,
              ...unitExtra,
              ...dateParams,
            }}
          />

          <BarChartCard
            title={`Hours per person — ${win.start} → ${win.end}`}
            data={personChartData}
            xKey="employee"
            series={[{ key: "hours", label: "Hours" }]}
            valueFormat="number"
            height={Math.min(420, Math.max(220, personChartData.length * 28))}
          />
          {!personChartData.length ? (
            <p className="text-sm text-muted-foreground">No ADP shift hours in this Period.</p>
          ) : null}
        </>
      )}
    </div>
  );
}
