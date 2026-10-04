"use client";

import { useMemo, useState } from "react";
import { useSuggestedHours } from "@/components/labor/SuggestedHoursContext";
import { LocalMultiSelect } from "@/components/filters/LocalMultiSelect";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { formatClockMin } from "@/lib/labor/coverage-model";
import { peopleToHire, shiftKind, summarizeOpenShifts, type Cell } from "@/lib/labor/open-shifts-insight";
import { DAY_NAMES } from "@/lib/labor/schedule-inputs";
import { inWeeks, weekOptions, weekStartOf } from "@/lib/labor/week-options";
import type { LocalMultiSelection } from "@/lib/tables/localMultiFilter";

const OPEN = LABOR_CHART_COLORS.openShift;
const DRAFT = LABOR_CHART_COLORS.draftShift;
const KINDS = [
  ["open", "Opener"],
  ["mid", "Mid"],
  ["close", "Closer"],
] as const;
const KIND_LABEL = { open: "Opener", mid: "Mid", close: "Closer" } as const;

function dayLabel(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y!, m! - 1, d!).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

const fmtH = (h: number) => `${Number(h.toFixed(1))}h`;

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex min-w-36 flex-col gap-0.5 rounded-md border border-border px-3 py-2">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-lg font-semibold tabular-nums text-foreground">{value}</span>
      {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
    </div>
  );
}

function GridCell({ cell, max }: { cell: Cell; max: number }) {
  if (!cell.shifts) return <TableCell className="text-center text-muted-foreground/60">—</TableCell>;
  return (
    <TableCell className="text-center tabular-nums">
      <span
        className="inline-flex min-w-16 justify-center rounded-md px-2 py-0.5"
        style={{ backgroundColor: `color-mix(in srgb, ${DRAFT} ${Math.round(12 + (cell.shifts / max) * 38)}%, transparent)` }}
      >
        {cell.shifts} · {fmtH(cell.hours)}
      </span>
    </TableCell>
  );
}

/** Upcoming shifts nobody is on yet, by weekday and shift type — a hiring view. */
export function OpenShiftsCard({ todayIso }: { todayIso: string }) {
  const { openShifts, capHours } = useSuggestedHours();
  const weeks = useMemo(
    () => {
      const thisWeek = weekStartOf(todayIso);
      const sunday = new Date(`${todayIso}T12:00:00Z`).getUTCDay() === 0;
      return weekOptions(openShifts.map((r) => r.date), todayIso).filter(
        (w) => w.start > thisWeek || (w.start === thisWeek && !sunday),
      );
    },
    [openShifts, todayIso],
  );
  const [selected, setSelected] = useState<LocalMultiSelection>(null);
  const picked = selected == null ? weeks : weeks.filter((w) => selected.includes(w.label));
  const starts = new Set(picked.map((w) => w.start));
  const rows = openShifts.filter((r) => inWeeks(r.date, starts)).sort((a, b) => a.date.localeCompare(b.date) || a.startMin - b.startMin);
  const s = summarizeOpenShifts(rows);
  const hires = peopleToHire(s, Math.max(1, picked.length), capHours);
  const max = Math.max(1, ...s.grid.flatMap((g) => KINDS.map(([k]) => g[k].shifts)));
  const suggested = rows.filter((r) => r.suggested).length;
  const range =
    picked.length === 1 ? picked[0]!.range : picked.length === weeks.length ? "all upcoming weeks" : `${picked.length} weeks`;

  return (
    <Card data-testid="open-shifts">
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium text-muted-foreground">Open shifts — {range}</CardTitle>
          <LocalMultiSelect label="Weeks" selected={selected} options={weeks.map((w) => w.label)} onChange={setSelected} />
        </div>
        <CardDescription>
          Upcoming shifts nobody is on yet: posted open in ADP, or needed by the draft with no one available (ADP
          unavailability, weekly hour caps, one shift a day). Follows your scheduling rules, including unsaved edits.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {rows.length ? (
          <>
            <div className="flex flex-wrap gap-2">
              <Stat
                label="Shifts to cover"
                value={`${s.unfilled.shifts}`}
                hint={`${fmtH(s.unfilled.hours)}${picked.length > 1 ? ` · ${fmtH(s.unfilled.hours / picked.length)}/week` : ""}${suggested ? ` · ${s.total.shifts} open, ${suggested} takeable` : ""}`}
              />
              <Stat
                label="People to hire (about)"
                value={`${hires}`}
                hint={`busiest day needs ${s.peak?.shifts ?? 0} different people · ${capHours}h/week cap`}
              />
              {s.peak ? (
                <Stat label="Busiest day" value={dayLabel(s.peak.date)} hint={`${s.peak.shifts} open shifts that day`} />
              ) : null}
              {suggested ? (
                <Stat label="ADP open, someone can take" value={`${suggested}`} hint="assign them in ADP" />
              ) : null}
            </div>

            <div className="max-w-2xl">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="h-8">Day</TableHead>
                    {KINDS.map(([k, label]) => (
                      <TableHead key={k} className="h-8 text-center">
                        {label}
                      </TableHead>
                    ))}
                    <TableHead className="h-8 text-right">Total</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {s.grid.map((g, i) => {
                    const t = KINDS.reduce((a, [k]) => ({ shifts: a.shifts + g[k].shifts, hours: a.hours + g[k].hours }), {
                      shifts: 0,
                      hours: 0,
                    });
                    return (
                      <TableRow key={DAY_NAMES[i]}>
                        <TableCell className="font-medium">{DAY_NAMES[i]}</TableCell>
                        {KINDS.map(([k]) => (
                          <GridCell key={k} cell={g[k]} max={max} />
                        ))}
                        <TableCell className="text-right tabular-nums">
                          {t.shifts ? `${t.shifts} · ${fmtH(t.hours)}` : <span className="text-muted-foreground/60">—</span>}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              <p className="mt-1 text-xs text-muted-foreground">
                Shifts · hours across the selected weeks. Opener starts by 7:30 AM, closer works until 8 PM or later.
              </p>
            </div>

            <div className="max-h-96 overflow-auto rounded-md border border-border">
              <Table>
                <TableHeader className="sticky top-0 bg-card">
                  <TableRow>
                    <TableHead className="h-8">Date</TableHead>
                    <TableHead className="h-8">Shift</TableHead>
                    <TableHead className="h-8">Time</TableHead>
                    <TableHead className="h-8 text-right">Hours</TableHead>
                    <TableHead className="h-8">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r, i) => (
                    <TableRow key={`${r.date}-${r.startMin}-${r.source}-${i}`}>
                      <TableCell className="whitespace-nowrap tabular-nums">{dayLabel(r.date)}</TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="h-5 px-1.5 text-[11px] font-normal">
                          {KIND_LABEL[shiftKind(r.startMin, r.endMin)]}
                        </Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums">
                        {formatClockMin(r.startMin)}–{formatClockMin(r.endMin)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{r.hours.toFixed(1)}</TableCell>
                      <TableCell>
                        <span className="flex flex-wrap items-center gap-2">
                          <Badge
                            variant="outline"
                            className="h-5 px-1.5 text-[11px] font-normal"
                            style={{ borderColor: r.source === "adp" ? OPEN : DRAFT, color: r.source === "adp" ? OPEN : DRAFT }}
                          >
                            {r.source === "adp" ? "Posted open in ADP" : "Draft · nobody available"}
                          </Badge>
                          {r.suggested ? (
                            <span className="text-xs text-muted-foreground">{r.suggested} can take it</span>
                          ) : null}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        ) : (
          <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
            No open shifts in the selected weeks — everyone needed is covered.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
