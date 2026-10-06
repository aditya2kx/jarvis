"use client";

import { useMemo, useState } from "react";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { LocalMultiSelect } from "@/components/filters/LocalMultiSelect";
import { useSuggestedHours } from "@/components/labor/SuggestedHoursContext";
import { showsPartTime } from "@/lib/filters/labor-type";
import { mergeHoursPerPerson, withOpenAndDraft, type PersonDayHours } from "@/lib/labor/hours-per-person";
import { weekOptions, inWeeks } from "@/lib/labor/week-options";
import type { LocalMultiSelection } from "@/lib/tables/localMultiFilter";

/** Hours per person for the weeks the operator picks (independent of the page Period). */
export function HoursPerPersonCard({
  actual,
  scheduled,
  open,
  laborTypes,
  todayIso,
  range,
}: {
  actual: PersonDayHours[];
  scheduled: PersonDayHours[];
  /** ADP open (unassigned) shift hours per day, upcoming only. */
  open: { date: string; hours: number }[];
  laborTypes: string[] | null;
  todayIso: string;
  /** Same horizon as the other charts: past weeks through the forward schedule horizon. */
  range: { start: string; end: string };
}) {
  const { byDayPerson } = useSuggestedHours();
  const weeks = useMemo(
    () => weekOptions([...actual, ...scheduled].map((r) => r.date.slice(0, 10)), todayIso, range),
    [actual, scheduled, todayIso, range],
  );
  const thisWeek = weeks.find((w) => w.current) ?? weeks[0];
  const [selected, setSelected] = useState<LocalMultiSelection>(thisWeek ? [thisWeek.label] : null);
  const picked = selected == null ? weeks : weeks.filter((w) => selected.includes(w.label));
  const starts = new Set(picked.map((w) => w.start));

  // Open and draft shifts are hourly (part-time) slots, like the Labor hours chart.
  const hourly = showsPartTime(laborTypes);
  const draftByPerson = new Map<string, number>();
  if (hourly) {
    for (const [iso, people] of byDayPerson) {
      if (!inWeeks(iso, starts)) continue;
      for (const [name, h] of people) draftByPerson.set(name, (draftByPerson.get(name) ?? 0) + h);
    }
  }
  const openHours = hourly
    ? open.filter((r) => inWeeks(r.date, starts)).reduce((a, r) => a + (Number(r.hours) || 0), 0)
    : 0;
  const chart = withOpenAndDraft(
    mergeHoursPerPerson(
      actual.filter((r) => inWeeks(r.date, starts)),
      scheduled.filter((r) => inWeeks(r.date, starts)),
      laborTypes,
      "9999-12-31",
    ),
    draftByPerson,
    openHours,
  );
  const hasSchedule = chart.series.some((s) => s.key.endsWith("_sched"));
  const rangeLabel =
    picked.length === 1 ? picked[0]!.range : picked.length === weeks.length ? "all weeks" : `${picked.length} weeks`;

  return (
    <div data-testid="labor-hours-per-person" className="flex flex-col gap-2">
      <BarChartCard
        title={`Hours per person — ${rangeLabel}`}
        subtitle={
          hasSchedule || chart.series.some((x) => x.pattern)
            ? "Clocked hours, then ADP scheduled for days not yet ingested, open shifts and suggested drafts"
            : "Clocked hours"
        }
        headerRight={
          <LocalMultiSelect
            label="Weeks"
            selected={selected}
            options={weeks.map((w) => w.label)}
            onChange={setSelected}
          />
        }
        data={chart.rows.map((r) => ({ ...r, name: r.employee.replace(", ", ",\n") }))}
        xKey="name"
        series={chart.series}
        stacked
        valueFormat="number"
        height={Math.min(420, Math.max(220, chart.rows.length * 28))}
      />
      {!chart.rows.length ? (
        <p className="text-sm text-muted-foreground">No clocked or scheduled hours in the selected weeks.</p>
      ) : null}
    </div>
  );
}
