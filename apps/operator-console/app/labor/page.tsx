import { laborByGrain, laborHoursPerPerson, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { dateSortKey } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { LaborHoursChart } from "@/components/labor/LaborHoursChart";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterPills } from "@/components/filters/FilterPills";
import { AggregationSelect } from "@/components/filters/AggregationSelect";
import { DateRangePicker } from "@/components/filters/DateRangePicker";
import { RANGE_PRESETS, formatBucket, wantsCustom } from "@/lib/filters/range";
import { resolvePageGrain, resolvePageRange } from "@/lib/filters/period";
import { parseBreakdown } from "@/lib/filters/sources";
import type { LaborDailyRow } from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

function goalFromConfig(rows: { key: string; value: string }[], key: string): number | undefined {
  const row = rows.find((r) => r.key === key);
  return row ? Number(row.value) : undefined;
}

export default async function LaborPage({
  searchParams,
}: {
  searchParams: Promise<{
    range?: string;
    from?: string;
    to?: string;
    grain?: string;
    breakdown?: string;
  }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range, sp.from, sp.to);
  const grain = await resolvePageGrain(sp.grain);
  const breakdown = parseBreakdown(sp.breakdown);
  const showCustomPicker = wantsCustom(sp.range) || win.preset === "custom";
  const dateParams: Record<string, string> = win.preset === "custom" ? { from: win.start, to: win.end } : {};
  const breakdownParam = breakdown ? "1" : "0";

  let rows: LaborDailyRow[] = [];
  let goalLaborPct: number | undefined;
  let hoursPerPerson: { employee: string; hours: number }[] = [];
  let error: string | undefined;
  try {
    const [labor, config, perPerson] = await Promise.all([
      laborByGrain(win, grain),
      storeConfig(DEFAULT_STORE),
      laborHoursPerPerson(win).catch(() => []),
    ]);
    rows = labor;
    goalLaborPct = goalFromConfig(config, "goal_labor_pct_max");
    hoursPerPerson = perPerson
      .map((p) => ({ employee: p.employee, hours: Number(p.hours) || 0 }))
      .filter((p) => p.hours > 0)
      .sort((a, b) => b.hours - a.hours);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const chartData = [...rows]
    .sort((a, b) => (dateSortKey(a.date) > dateSortKey(b.date) ? 1 : -1))
    .map((r) => ({
      date: formatBucket(r.date, grain),
      total_hours: r.total_hours != null ? Number(Number(r.total_hours).toFixed(1)) : null,
      parttime_hours: r.hourly_hours != null ? Number(Number(r.hourly_hours).toFixed(1)) : null,
      fulltime_hours: r.fulltime_hours != null ? Number(Number(r.fulltime_hours).toFixed(1)) : null,
      labor_pct: r.labor_pct != null ? Number(r.labor_pct) : null,
      hourly_pct: r.hourly_pct != null ? Number(r.hourly_pct) : null,
      fulltime_pct: r.fulltime_pct != null ? Number(r.fulltime_pct) : null,
    }));

  const personChartData = hoursPerPerson.map((p) => ({
    employee: p.employee,
    hours: Number(p.hours.toFixed(1)),
  }));

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Labor"
        subtitle={`Historical ADP hours · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <AggregationSelect
              value={grain}
              basePath="/labor"
              extraParams={{ range: win.preset, breakdown: breakdownParam, ...dateParams }}
            />
            <FilterSelect
              label="Period"
              param="range"
              value={showCustomPicker ? "custom" : win.preset}
              options={RANGE_PRESETS}
              basePath="/labor"
              extraParams={{ grain, breakdown: breakdownParam }}
            />
            {showCustomPicker ? (
              <DateRangePicker
                basePath="/labor"
                from={win.start}
                to={win.end}
                committed={win.preset === "custom"}
                extraParams={{ grain, breakdown: breakdownParam }}
              />
            ) : null}
          </>
        }
      />

      <FilterPills
        label="View"
        param="breakdown"
        value={breakdownParam}
        options={[
          { value: "0", label: "Aggregate" },
          { value: "1", label: "PT / FT" },
        ]}
        basePath="/labor"
        extraParams={{ range: win.preset, grain, ...dateParams }}
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <LaborHoursChart
            data={chartData}
            breakdown={breakdown}
            grain={grain}
            goalLaborPct={goalLaborPct}
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
