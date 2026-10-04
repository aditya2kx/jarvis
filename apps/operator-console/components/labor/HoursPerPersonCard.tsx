"use client";

import { useMemo, useState } from "react";
import { BarChartCard } from "@/components/charts/BarChartCard";
import { LocalMultiSelect } from "@/components/filters/LocalMultiSelect";
import { mergeHoursPerPerson, type PersonDayHours } from "@/lib/labor/hours-per-person";
import { weekOptions, inWeeks } from "@/lib/labor/week-options";
import type { LocalMultiSelection } from "@/lib/tables/localMultiFilter";

/** Hours per person for the weeks the operator picks (independent of the page Period). */
export function HoursPerPersonCard({
  actual,
  scheduled,
  laborTypes,
  todayIso,
}: {
  actual: PersonDayHours[];
  scheduled: PersonDayHours[];
  laborTypes: string[] | null;
  todayIso: string;
}) {
  const weeks = useMemo(
    () => weekOptions([...actual, ...scheduled].map((r) => r.date.slice(0, 10)), todayIso),
    [actual, scheduled, todayIso],
  );
  const thisWeek = weeks.find((w) => w.current) ?? weeks[0];
  const [selected, setSelected] = useState<LocalMultiSelection>(thisWeek ? [thisWeek.label] : null);
  const picked = selected == null ? weeks : weeks.filter((w) => selected.includes(w.label));
  const starts = new Set(picked.map((w) => w.start));

  const chart = mergeHoursPerPerson(
    actual.filter((r) => inWeeks(r.date, starts)),
    scheduled.filter((r) => inWeeks(r.date, starts)),
    laborTypes,
    "9999-12-31",
  );
  const hasSchedule = chart.series.some((s) => s.key.endsWith("_sched"));
  const range =
    picked.length === 1 ? picked[0]!.range : picked.length === weeks.length ? "all weeks" : `${picked.length} weeks`;

  return (
    <div data-testid="labor-hours-per-person" className="flex flex-col gap-2">
      <BarChartCard
        title={`Hours per person — ${range}`}
        subtitle={hasSchedule ? "Clocked hours + ADP scheduled for days not yet ingested" : "Clocked hours"}
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
