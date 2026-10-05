import {
  adpHoursScrapedAt,
  adpUnavailability,
  adpDirectoryRoster,
  adpScheduleScrapedAt,
  adpScheduleHorizonEnd,
  laborActualShiftDays,
  laborActualsThrough,
  laborByGrain,
  laborConcurrentByGrain,
  laborDemandProfile,
  laborHoursPerPersonDaily,
  laborOpenShiftDays,
  laborOpenShiftHoursByGrain,
  laborPunchDays,
  laborPunchGaps,
  laborScheduledHoursByGrain,
  laborScheduledShiftDays,
  laborSoloHoursPerPerson,
  laborWageRates,
  storeConfig,
  type LaborWageRates,
  upcomingRestockDates,
  type UnavailabilityRow,
} from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey, formatCents } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { FEATURES } from "@/lib/config/features";
import type { DemandCell } from "@/lib/labor/staffing-need";
import { HoursPerPersonCard } from "@/components/labor/HoursPerPersonCard";
import { OpenShiftsCard } from "@/components/labor/OpenShiftsCard";
import { CollapsibleSection } from "@/components/shell/CollapsibleSection";
import { weekStartOf } from "@/lib/labor/week-options";
import { upcomingPushRows } from "@/lib/labor/schedule-push-store";
import type { PushRow } from "@/lib/labor/schedule-push";
import { LaborHoursChart } from "@/components/labor/LaborHoursChart";
import { LaborWagesChart } from "@/components/labor/LaborWagesChart";
import { SuggestedHoursProvider } from "@/components/labor/SuggestedHoursContext";
import { LaborWeeklyHoursGoal } from "@/components/labor/LaborWeeklyHoursGoal";
import { LaborConcurrentChart } from "@/components/labor/LaborConcurrentChart";
import { LaborCoveragePanel } from "@/components/labor/LaborCoveragePanel";
import { PunchGapsPanel } from "@/components/labor/PunchGapsPanel";
import { buildPunchGaps, mergePunchDays, type PunchGap } from "@/lib/labor/punch-gaps";
import { scheduleRulesHistory } from "@/lib/labor/schedule-rules-store";
import { ruleUnavailability, type RulesVersion } from "@/lib/labor/schedule-inputs";
import { SyncAdpButton } from "@/components/labor/SyncAdpButton";
import { AdpAvailabilityCard } from "@/components/labor/AdpAvailabilityCard";
import type { ScheduledShift } from "@/lib/labor/unavailability";
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
  shiftCalendarDate,
  formatBucket,
  wantsCustom,
  type DateWindow,
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
  clockedHoursTargetDate,
  laborChartWindow,
  periodIncludesToday,
  scheduleTakesOverFrom,
  scheduledShiftWindow,
  forwardHorizonEnd,
} from "@/lib/labor/actual-schedule-windows";
import {
  aggregateScheduledDays,
  rollConcurrentToGrain,
} from "@/lib/labor/schedule-aggregate";
import {
  showChartOpenShifts,
  showChartSchedule,
  showCoverageSchedule,
} from "@/lib/labor/schedule-fetch-gates";
import { summarizeSoloHours } from "@/lib/labor/solo-hours";
import {
  mergeHoursPerPerson,
  personChartSupportsGrain,
  personHoursByGrain,
  type PersonDayHours,
} from "@/lib/labor/hours-per-person";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "@/components/tables/DataTable";
import type {
  AdpRosterRow,
  LaborActualShiftDayRow,
  LaborConcurrentRow,
  LaborDailyRow,
  LaborOpenShiftDayRow,
  LaborOpenShiftHoursRow,
  LaborScheduledHoursRow,
  LaborScheduledShiftDayRow,
  LaborSoloHoursRow,
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
    person?: string;
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
  const personExtra: Record<string, string> = sp.person ? { person: sp.person } : {};

  const includesToday = periodIncludesToday(win);
  let chartWin = win;
  let punchWin: DateWindow | null = null;
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
  let personActualDays: PersonDayHours[] = [];
  let personScheduledDays: PersonDayHours[] = [];
  let weeklyActualDays: PersonDayHours[] = [];
  let weeklyScheduledDays: PersonDayHours[] = [];
  let weeklyOpenDays: { date: string; hours: number }[] = [];
  let weeklyRange = { start: "", end: "" };
  let hoursScrapedAt: string | null = null;
  let coverageActuals: LaborActualShiftDayRow[] = [];
  let coverageScheduled: LaborScheduledShiftDayRow[] = [];
  let soloRows: LaborSoloHoursRow[] = [];
  let openHoursRows: LaborOpenShiftHoursRow[] = [];
  let coverageOpen: LaborOpenShiftDayRow[] = [];
  let punchGaps: PunchGap[] = [];
  let coverageDemand: DemandCell[] = [];
  let deliveryDates: string[] = [];
  let rulesHistory: RulesVersion[] = [];
  let unavailability: UnavailabilityRow[] = [];
  let scheduleReadAt: string | null = null;
  let upcomingShifts: ScheduledShift[] = [];
  let adpRosterRows: AdpRosterRow[] = [];
  let wageRates: LaborWageRates = { byName: {}, avgPartTime: null };
  let savedPushRows: PushRow[] = [];
  let error: string | undefined;
  try {
    // When Period includes today, extend charts through the latest ADP scheduled
    // date or FORWARD_WEEKS ahead, whichever is later (any Aggregation).
    const todayIso = chicagoTodayIso();
    const scheduleHorizonEnd = includesToday
      ? forwardHorizonEnd(todayIso, await adpScheduleHorizonEnd().catch(() => null))
      : null;
    // Hand off from actual to scheduled where the punches actually end, not at
    // a fixed "yesterday" — otherwise the evening's freshly-ingested hours are
    // overdrawn by their own schedule. Falls back to today if unreadable.
    const boundaryIso = scheduleTakesOverFrom(
      todayIso,
      await laborActualsThrough().catch(() => null),
    );
    punchWin = actualPunchWindow(win, boundaryIso);
    chartWin = laborChartWindow(win, todayIso, scheduleHorizonEnd);
    const schedWin = scheduledShiftWindow(win, boundaryIso, scheduleHorizonEnd, todayIso);
    // Hours per person has its own week picker: last 8 weeks through the later of the charts' end and the forward horizon.
    const forward =
      scheduleHorizonEnd ?? forwardHorizonEnd(todayIso, await adpScheduleHorizonEnd().catch(() => null));
    const weeklyHorizon = chartWin.end > forward ? chartWin.end : forward;
    const weeklyWin: DateWindow = {
      start: weekStartOf(shiftCalendarDate(todayIso, "day", -7 * 8)),
      end: weeklyHorizon,
      label: "Hours per person",
      preset: "custom",
    };
    weeklyRange = { start: weeklyWin.start, end: weeklyWin.end };
    const weeklyPunchWin = actualPunchWindow(weeklyWin, boundaryIso);
    const weeklySchedWin = scheduledShiftWindow(weeklyWin, boundaryIso, weeklyHorizon, todayIso);
    // Charts: Hour grain omits schedule stacks (#227). Coverage is day-level —
    // show ADP schedule whenever the schedule window is non-null (any Aggregation;
    // future-only Periods included) — Issue #243.
    const chartSchedule = showChartSchedule({
      includesToday,
      hasSchedWin: schedWin != null,
      grain,
    });
    const coverageSchedule = showCoverageSchedule({
      hasSchedWin: schedWin != null,
    });
    // Open shifts (#342): chart = upcoming only (schedWin); coverage = whole strip,
    // so a past slot nobody filled still shows as a gap.
    const chartOpen = showChartOpenShifts({
      includesToday,
      hasSchedWin: schedWin != null,
      grain,
    });

    const [
      labor,
      config,
      perPersonDays,
      concurrent,
      schedHours,
      schedDays,
      coverageSchedDays,
      hoursScraped,
      actualShiftDays,
      solo,
      openHours,
      openDays,
      gapRows,
      gapCoworkers,
      punchDays,
      demand,
      restockDates,
      rulesVersions,
      unavailRows,
      scheduleRead,
      upcomingRows,
      weeklyActual,
      weeklySched,
      weeklyOpen,
      directoryRoster,
      rates,
      pushRows,
    ] = await Promise.all([
      punchWin ? laborByGrain(punchWin, grain, stat) : Promise.resolve([]),
      storeConfig(DEFAULT_STORE),
      punchWin ? laborHoursPerPersonDaily(punchWin).catch(() => []) : Promise.resolve([]),
      punchWin
        ? laborConcurrentByGrain(punchWin, grain, stat).catch(() => [])
        : Promise.resolve([]),
      chartSchedule && schedWin
        ? laborScheduledHoursByGrain(schedWin, grain, { excludePto }).catch(() => [])
        : Promise.resolve([]),
      (chartSchedule || coverageSchedule) && schedWin
        ? laborScheduledShiftDays(schedWin, { store: DEFAULT_STORE, excludePto }).catch(
            () => [],
          )
        : Promise.resolve([]),
      coverageSchedule
        ? laborScheduledShiftDays(chartWin, {
            store: DEFAULT_STORE,
            excludePto,
            allowPast: true,
          }).catch(
            () => [],
          )
        : Promise.resolve([]),
      adpHoursScrapedAt().catch(() => null),
      punchWin ? laborActualShiftDays(punchWin).catch(() => []) : Promise.resolve([]),
      // Solo hours are punch-derived, so they only exist for days already
      // ingested — same window as the other actuals, never the schedule window.
      punchWin
        ? laborSoloHoursPerPerson(punchWin).catch(() => [])
        : Promise.resolve([]),
      chartOpen && schedWin
        ? laborOpenShiftHoursByGrain(schedWin, grain).catch(() => [])
        : Promise.resolve([]),
      laborOpenShiftDays(chartWin).catch(() => []),
      // Gaps are keyed on the Period itself (not the chart/punch windows): a
      // forgotten clock-out is a past-day fact regardless of schedule handoff.
      laborPunchGaps(win, DEFAULT_STORE).catch(() => []),
      laborActualShiftDays(win).catch(() => []),
      laborPunchDays(win, DEFAULT_STORE).catch(() => []),
      laborDemandProfile(todayIso).catch(() => []),
      upcomingRestockDates(DEFAULT_STORE, todayIso).catch(() => []),
      scheduleRulesHistory(DEFAULT_STORE).catch(() => []),
      adpUnavailability(DEFAULT_STORE).catch(() => []),
      adpScheduleScrapedAt().catch(() => null),
      laborScheduledShiftDays(
        { start: todayIso, end: shiftCalendarDate(todayIso, "day", 56), label: "Next 8 weeks", preset: "custom" },
        { store: DEFAULT_STORE, excludePto: true },
      ).catch(() => []),
      weeklyPunchWin ? laborHoursPerPersonDaily(weeklyPunchWin).catch(() => []) : Promise.resolve([]),
      weeklySchedWin
        ? laborScheduledShiftDays(weeklySchedWin, { store: DEFAULT_STORE, excludePto }).catch(() => [])
        : Promise.resolve([]),
      weeklySchedWin ? laborOpenShiftDays(weeklySchedWin).catch(() => []) : Promise.resolve([]),
      adpDirectoryRoster(DEFAULT_STORE).catch(() => []),
      laborWageRates().catch(() => ({ byName: {}, avgPartTime: null })),
      upcomingPushRows(DEFAULT_STORE, weekStartOf(todayIso)).catch(() => []),
    ]);
    savedPushRows = pushRows;
    adpRosterRows = directoryRoster;
    wageRates = rates;
    weeklyOpenDays = weeklyOpen.map((r) => ({ date: r.date, hours: Number(r.scheduled_hours) || 0 }));
    weeklyActualDays = weeklyActual;
    weeklyScheduledDays = weeklySched.map((r) => ({
      date: r.date,
      employee: r.employee,
      labor_bucket: r.labor_bucket,
      hours: r.scheduled_hours,
    }));
    punchGaps = mergePunchDays(buildPunchGaps(gapRows, gapCoworkers), punchDays, gapCoworkers);
    rulesHistory = rulesVersions;
    unavailability = unavailRows;
    scheduleReadAt = scheduleRead;
    upcomingShifts = upcomingRows;
    soloRows = solo;
    openHoursRows = openHours;
    coverageOpen = openDays;
    coverageDemand = demand;
    deliveryDates = restockDates;
    rows = labor;
    concurrentRows = concurrent;
    scheduledHoursRows = schedHours;
    coverageScheduled = coverageSchedule ? coverageSchedDays : [];
    coverageActuals = actualShiftDays;
    scheduledConcurrentByBucket = chartSchedule
      ? rollConcurrentToGrain(aggregateScheduledDays(schedDays), grain)
      : [];
    hoursScrapedAt = hoursScraped;
    goalLaborHoursWeek = goalFromConfig(config, "goal_labor_hours_week");
    personActualDays = perPersonDays;
    personScheduledDays = schedDays.map((r) => ({
      date: r.date,
      employee: r.employee,
      labor_bucket: r.labor_bucket,
      hours: r.scheduled_hours,
    }));
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
          parttime_cost: r.hourly_labor_cost != null ? Number(r.hourly_labor_cost) : null,
          fulltime_cost: r.fulltime_labor_cost != null ? Number(r.fulltime_labor_cost) : null,
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
          parttime_sched_cost: r.parttime_cost != null ? Number(r.parttime_cost) : null,
          fulltime_sched_cost: r.fulltime_cost != null ? Number(r.fulltime_cost) : null,
        },
      ] as const;
    }),
  );

  const openByBucket = new Map(
    openHoursRows.map((r) => [
      isoKey(r.date),
      {
        open_hours: r.open_hours != null ? Number(Number(r.open_hours).toFixed(1)) : null,
        open_slots: r.open_slots != null ? Number(r.open_slots) : null,
      },
    ] as const),
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
    const o = openByBucket.get(iso);
    return {
      date: formatBucket(iso, grain, grain === "day" ? { weekday: true } : undefined),
      bucket_iso: iso,
      open_hours: o?.open_hours ?? null,
      open_slots: o?.open_slots ?? null,
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

  const wagesChartData = bucketIsos.map((iso) => {
    const a = actualByBucket.get(iso);
    const s = schedHoursByBucket.get(iso);
    const openHours = openByBucket.get(iso)?.open_hours ?? null;
    return {
      date: formatBucket(iso, grain, grain === "day" ? { weekday: true } : undefined),
      bucket_iso: iso,
      parttime_cost: a?.parttime_cost ?? null,
      fulltime_cost: a?.fulltime_cost ?? null,
      net_sales: a?.net_sales ?? null,
      parttime_sched_cost: s?.parttime_sched_cost ?? null,
      fulltime_sched_cost: s?.fulltime_sched_cost ?? null,
      open_cost:
        openHours != null && wageRates.avgPartTime != null ? openHours * wageRates.avgPartTime : null,
    };
  });

  const concurrentChartData = bucketIsos.map((iso) => {
    const a = concurrentActualByBucket.get(iso);
    const s = concurrentSchedByBucket.get(iso);
    return {
      date: formatBucket(iso, grain, grain === "day" ? { weekday: true } : undefined),
      bucket_iso: iso,
      parttime_concurrent: a?.parttime_concurrent ?? null,
      fulltime_concurrent: a?.fulltime_concurrent ?? null,
      total_concurrent: a?.total_concurrent ?? null,
      parttime_scheduled_concurrent: s?.parttime_concurrent ?? null,
      fulltime_scheduled_concurrent: s?.fulltime_concurrent ?? null,
      total_scheduled_concurrent: s?.total_concurrent ?? null,
    };
  });

  const perPersonChart = mergeHoursPerPerson(
    personActualDays,
    personScheduledDays,
    laborTypes,
    win.end,
  );
  // Picker lists whoever has hours under the current filters; a stale or
  // missing `person` falls back to the top of the per-person chart.
  const personOptions = perPersonChart.rows.map((r) => ({
    value: r.employee,
    label: r.employee,
  }));
  const selectedPerson =
    personOptions.find((o) => o.value === sp.person)?.value ?? personOptions[0]?.value;
  const personChartData =
    selectedPerson && personChartSupportsGrain(grain)
      ? personHoursByGrain({
          actual: personActualDays,
          scheduled: personScheduledDays,
          employee: selectedPerson,
          grain,
          stat,
          win: chartWin,
        })
      : [];

  // Summarised before the columns are built: the Remote column is only rendered
  // when the window actually contains remote shifts.
  const soloSummary = summarizeSoloHours(soloRows);
  // Solo hours are a pay input, so the table shows the split every employee is
  // paid on rather than a single derived number: solo + team always equals total.
  const soloColumns: ColumnDef<LaborSoloHoursRow>[] = [
    { accessorKey: "employee", header: "Employee" },
    {
      accessorKey: "solo_hours",
      header: "Solo hours",
      meta: { format: { kind: "number", digits: 2, minDigits: 2 } },
    },
    {
      accessorKey: "team_hours",
      header: "Team hours",
      meta: { format: { kind: "number", digits: 2, minDigits: 2 } },
    },
    ...(soloSummary.remoteHours > 0
      ? [
          {
            accessorKey: "remote_hours",
            header: "Remote hours",
            meta: { format: { kind: "number" as const, digits: 2, minDigits: 2 } },
          } satisfies ColumnDef<LaborSoloHoursRow>,
        ]
      : []),
    {
      accessorKey: "total_hours",
      header: "Total hours",
      meta: { format: { kind: "number", digits: 2, minDigits: 2 } },
    },
    {
      accessorKey: "base_rate_dollars",
      header: "Base rate",
      meta: { format: { kind: "dollars" } },
    },
    {
      accessorKey: "eligible",
      header: "Premium",
      meta: { format: { kind: "flag", trueLabel: "Eligible", falseLabel: "—" } },
    },
    {
      accessorKey: "premium_cents",
      header: "Premium owed",
      meta: { format: { kind: "cents" } },
    },
  ];

  const statPrefix = showStat && stat === "avg" ? "Average " : showStat ? "Total " : "";
  const statSubtitle =
    showStat && stat === "avg"
      ? grain === "hour"
        ? "Per day in Period"
        : "Per weekday in Period"
      : showStat
        ? "Sum across Period"
        : undefined;
  // Anyone scheduled in ADP plus every hourly person the ADP Directory lists as Active; a Directory
  // status other than Active (Terminated, Leave of absence) drops even the scheduled.
  // Empty means both reads failed — keep the unfiltered roster then.
  const statusOf = new Map(adpRosterRows.map((r) => [r.employee, r.employment_status?.toLowerCase() ?? null]));
  const staffNames = [
    ...new Set([
      ...upcomingShifts.map((s) => s.employee),
      ...adpRosterRows.filter((r) => r.employment_status?.toLowerCase() === "active").map((r) => r.employee),
    ]),
  ].filter((name) => (statusOf.get(name) ?? "active") === "active");
  const activeStaff = staffNames.length ? staffNames : undefined;

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
                ...personExtra,
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
                ...personExtra,
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
                  ...personExtra,
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
                ...personExtra,
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
                  ...personExtra,
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
                ...personExtra,
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
                ...personExtra,
              }}
            />
            <LaborWeeklyHoursGoal current={goalLaborHoursWeek} />
            <SyncAdpButton
              lastScrapedAt={hoursScrapedAt}
              targetDate={clockedHoursTargetDate({
                todayIso: chicagoTodayIso(),
                coverageDay: sp.day,
              })}
            />
          </>
        }
      />

      <div
        role="note"
        className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
      >
        <p>
          <span className="font-medium text-foreground">Actual</span> (solid colors) =
          ADP clocked hours, through the last day they have been ingested for.{" "}
          <span className="font-medium text-foreground">Scheduled</span> (slate) stacks
          on the hours / concurrent charts only for days after that, through the latest
          ADP scheduled dates when the Period includes today (not only through Period
          end) — so a day never shows both, and once the evening ingest lands the day
          switches from schedule to what was actually worked.{" "}
          <span className="font-medium text-foreground">Open (unassigned)</span> ADP
          shifts stack on top in violet hatch on the Hours chart for those same upcoming
          days (not on Hour of day or Weekday); hover shows the Total if filled, which
          is what the weekly Goal compares against. Staffing coverage also keeps past
          open slots as unfilled gaps. Hover
          also shows{" "}
          <span className="font-medium text-foreground">Total (combined)</span> vs
          weekly Goal.{" "}
          <span className="font-medium text-foreground">% of net sales</span> on the
          Hours chart uses completed days only (no schedule stacks) and current
          ADP wage rates × clocked hours over Square net sales (not frozen
          model-sheet labor $).{" "}
          {grain === "hour"
            ? "Hour of day allocates clocked shifts across clock hours and pairs with Sales ops-hour net sales for %; schedule stacks are hidden. "
            : ""}
          {showStat
            ? `Stat Average = typical ${grain === "hour" ? "hour across days" : "weekday"} in the Period; Total = sum across the Period. `
            : ""}
          {goalLaborHoursWeek != null && !Number.isNaN(Number(goalLaborHoursWeek))
            ? `Weekly Goal (${Number(goalLaborHoursWeek)} hrs) is the gold dashed line on Aggregation=Weekly in Hours mode — same value as Home Goals. `
            : ""}
          <span className="font-medium text-foreground">Avg concurrent</span> bars =
          actual only (schedule stays in the hover). PT/FT concurrent is hours ÷ that
          bucket&apos;s first→last span (one full-timer ≈ 1), not diluted by store-open
          hours from the other bucket.{" "}
          <span className="font-medium text-foreground">Staffing coverage</span> is a
          day strip for the Period (extended through scheduled shifts when today is
          included) with a headcount ribbon and person swimlanes — Aggregation does not
          apply here, so scheduled shifts still show when Aggregation is Hour (or any
          other grain) whenever ADP has them in the schedule window. Scroll the chips when
          the range is long. Use{" "}
          <span className="font-medium text-foreground">Sync ADP</span> after editing the
          ADP schedule or fixing punches — one ADP login refreshes clocked hours, assigned
          and open shifts, payroll earnings and pay rates. Status under the button shows
          starting / syncing / done / error without blocking the rest of the page. Hover it
          when idle for last-synced time. Paid PTO is included in
          scheduled hours by default (matches ADP); use the PTO filter to exclude it.
          Per-person hours stack clocked hours with scheduled hours (slate) for the
          days not yet ingested, through Period end. Pick a{" "}
          <span className="font-medium text-foreground">Person</span> below it to see
          their hours by the page&apos;s Aggregation, on the same axis and handoff as
          the Hours chart.
        </p>
      </div>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <SuggestedHoursProvider>
          <CollapsibleSection id="labor-hours" title="Labor hours">
          <LaborHoursChart
            data={chartData}
            laborTypes={laborTypes}
            grain={grain}
            goalLaborHoursWeek={goalLaborHoursWeek}
            unit={chartUnit}
            titlePrefix={statPrefix}
            subtitle={statSubtitle}
            stat={stat === "avg" ? "avg" : "total"}
            period={{ start: chartWin.start, end: chartWin.end }}
          />
          </CollapsibleSection>

          <CollapsibleSection id="labor-wages" title="Labor wages">
          <LaborWagesChart
            data={wagesChartData}
            rates={wageRates}
            laborTypes={laborTypes}
            grain={grain}
            titlePrefix={statPrefix}
            subtitle={statSubtitle}
            stat={stat === "avg" ? "avg" : "total"}
            period={{ start: chartWin.start, end: chartWin.end }}
          />
          </CollapsibleSection>

          <CollapsibleSection id="concurrent" title="On the floor">
          <LaborConcurrentChart
            data={concurrentChartData}
            laborTypes={laborTypes}
            grain={grain}
            titlePrefix={statPrefix}
            subtitle={statSubtitle}
            stat={stat === "avg" ? "avg" : "total"}
            period={{ start: chartWin.start, end: chartWin.end }}
          />
          </CollapsibleSection>

          <CollapsibleSection id="coverage" title="Coverage & shift draft">
          <LaborCoveragePanel
            win={chartWin}
            actuals={coverageActuals}
            scheduled={coverageScheduled}
            open={coverageOpen}
            laborTypes={laborTypes}
            demand={coverageDemand}
            goalHoursWeek={goalLaborHoursWeek}
            deliveryDates={deliveryDates}
            rulesHistory={rulesHistory}
            unavailability={unavailability}
            activeStaff={activeStaff}
            savedPushRows={savedPushRows}
            adpWriteEnabled={FEATURES.adpScheduleWrite}
          />
          </CollapsibleSection>

          <CollapsibleSection id="availability" title="ADP availability">
          <AdpAvailabilityCard
            rows={[
              ...unavailability,
              ...ruleUnavailability(rulesHistory[0]?.rules.staffRules ?? []).map(
                (r): UnavailabilityRow => ({ ...r, expires_at_ct: null, hours_left: null, scraped_at: null }),
              ),
            ]}
            shifts={upcomingShifts}
            roster={activeStaff ?? []}
            approveEnabled={FEATURES.adpUnavailabilityApprove}
            lastReadAt={scheduleReadAt}
            todayIso={chicagoTodayIso()}
          />
          </CollapsibleSection>

          <CollapsibleSection id="hours-per-person" title="Hours per person">
          <HoursPerPersonCard
            actual={weeklyActualDays}
            scheduled={weeklyScheduledDays}
            open={weeklyOpenDays}
            laborTypes={laborTypes}
            todayIso={chicagoTodayIso()}
            range={weeklyRange}
          />
          </CollapsibleSection>

          <CollapsibleSection id="open-shifts" title="Open shifts">
          <OpenShiftsCard todayIso={chicagoTodayIso()} />
          </CollapsibleSection>

          {selectedPerson ? (
            <CollapsibleSection id="one-person" title="One person">
            <div data-testid="labor-hours-one-person" className="flex flex-col gap-2">
              {personChartSupportsGrain(grain) ? (
                <LaborHoursChart
                  data={personChartData}
                  laborTypes={laborTypes}
                  grain={grain}
                  titlePrefix={statPrefix}
                  subtitle={statSubtitle}
                  person={selectedPerson}
                  headerRight={
                    <FilterSelect
                      label="Person"
                      param="person"
                      value={selectedPerson}
                      options={personOptions}
                      basePath="/labor"
                      extraParams={{
                        range: win.preset,
                        grain,
                        ...statExtra,
                        ...laborTypeExtra,
                        ...ptoExtra,
                        ...unitExtra,
                        ...dateParams,
                        ...dayExtra,
                      }}
                    />
                  }
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Hours for one person are shown by day, week, month, weekday or Entire
                  period — pick one of those in Aggregation (Hour of day is store-wide
                  only).
                </p>
              )}
            </div>
            </CollapsibleSection>
          ) : null}

          {punchGaps.length ? (
            <CollapsibleSection id="punch-gaps" title="Punch gaps">
          <PunchGapsPanel gaps={punchGaps} periodLabel={`${win.start} → ${win.end}`} />
          </CollapsibleSection>
          ) : null}

          <CollapsibleSection id="solo" title={`Solo vs team hours — ${win.start} → ${win.end}`}>
          <div className="flex flex-col gap-2">
            {soloSummary.rows.length ? (
              <>
                <DataTable
                  columns={soloColumns}
                  data={soloSummary.rows}
                  pinLeft={["employee"]}
                />
                <p className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">Solo</span> hours are
                  minutes an employee was the only person in the shop, in contiguous
                  blocks of at least the configured minimum — solo + team always equals
                  total. Solo hours accrue for everyone, but{" "}
                  <span className="font-medium text-foreground">Premium</span> is only
                  marked when the employee is on the eligible base rate{" "}
                  <em>and</em> the hours fall on or after the policy&apos;s effective
                  date — so someone on the eligible rate can still show no premium for
                  solo hours worked before it, and anyone already above the rate never
                  earns one. Premium owed is what moves to the higher rate in ADP for
                  the pay period — key it from the{" "}
                  <span className="font-medium text-foreground">Payroll</span> page,
                  which is scoped to pay-period boundaries rather than this Period
                  filter.
                </p>
                {soloSummary.remoteHours > 0 ? (
                  <p className="text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">Remote</span> hours
                    are shifts worked away from the shop. They count as team hours and
                    are paid normally, but they are not floor coverage: a remote
                    colleague does not stop someone from being solo, and remote time
                    never earns the premium itself. Rows with remote hours and no solo
                    hours are listed for that context. Remote shifts are annotated in{" "}
                    <code className="font-mono">solo_shift_remote_days</code>; an
                    unannotated shift counts as on the floor.
                  </p>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                Nobody worked alone in this Period.
              </p>
            )}
            {soloSummary.people > 0 ? (
              <p className="text-xs text-muted-foreground">
                {soloSummary.soloHours.toFixed(2)} solo hours across{" "}
                {soloSummary.people}{" "}
                {soloSummary.people === 1 ? "person" : "people"} ·{" "}
                {formatCents(soloSummary.premiumCents)} premium owed.
              </p>
            ) : null}
          </div>
          </CollapsibleSection>
        </SuggestedHoursProvider>
      )}
    </div>
  );
}
