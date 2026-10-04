"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import {
  axisBounds,
  buildOpenLanesForDate,
  buildPersonDaysForDate,
  coverageNarrative,
  coverageStripDates,
  dayChipSummary,
  filterScheduledForCoverage,
  formatClockMin,
  isOpenLane,
  occupancySeries,
  peopleActiveAt,
  defaultCoverageDay,
  segmentLeftPct,
  segmentWidthPct,
  snapMinute,
  type ActualShiftInput,
  type CoverageDayChip,
  type CoverageKind,
  type CoveragePersonDay,
  type OccupancyPoint,
  type OpenShiftInput,
  type ScheduledShiftInput,
} from "@/lib/labor/coverage-model";
import {
  isoWeekdayMon0,
  needSeries,
  onFloor,
  shortNarrative,
  shortPersonHours,
  shortWindows,
  type DemandCell,
} from "@/lib/labor/staffing-need";
import { addDay, emptyBreakdown, requiredByRule } from "@/lib/labor/draft-breakdown";
import { adpRoster, draftDay, fillOpenShift, type DraftShift } from "@/lib/labor/shift-draft";
import { blocksOn, type UnavailabilityInput } from "@/lib/labor/unavailability";
import {
  applyDayRules,
  DEFAULT_RULES,
  staffLimits,
  type DayRule,
  type RulesVersion,
  type ScheduleRules,
  type StaffingBasics,
} from "@/lib/labor/schedule-inputs";
import { payPeriodStartFor } from "@/lib/payroll/openPeriod";
import { DRAFT_COLOR, ShiftDraftSummary } from "@/components/labor/ShiftDraftSummary";
import { useSuggestedHours } from "@/components/labor/SuggestedHoursContext";
import type { OpenShift } from "@/lib/labor/open-shifts-insight";
import { ScheduleInputsPanel } from "@/components/labor/ScheduleInputsPanel";
import { Button } from "@/components/ui/button";
import { chicagoTodayIso, shiftCalendarDate, type DateWindow } from "@/lib/filters/range";
import { cn } from "@/lib/utils";

const PT = LABOR_CHART_COLORS.parttimeActual;
const FT = LABOR_CHART_COLORS.fulltimeActual;
const SCHED = LABOR_CHART_COLORS.parttimeScheduled;
const OPEN = LABOR_CHART_COLORS.openShift;
const OPEN_HATCH = `repeating-linear-gradient(-45deg, ${OPEN}40, ${OPEN}40 2px, transparent 2px, transparent 4px)`;
const NEED = LABOR_CHART_COLORS.goalLine;
const SHORT_TINT = "rgb(244 63 94 / 0.16)";

/** Axis must span open hours so the need line isn't clipped on sparse days. */
function withOpenHours(b: { startMin: number; endMin: number }, s: StaffingBasics) {
  return {
    startMin: Math.min(b.startMin, Math.floor(s.openMin / 60) * 60),
    endMin: Math.max(b.endMin, Math.ceil(s.closeMin / 60) * 60),
  };
}

type NeedCtx = { staffing: StaffingBasics; dayRules: DayRule[]; deliveries: ReadonlySet<string> };

const floorOf = (s: StaffingBasics) => [{ fromMin: s.openMin, toMin: s.closeMin, min: s.minPeople }];

function dayCoverage(
  iso: string,
  people: CoveragePersonDay[],
  demand: DemandCell[] | undefined,
  ctx: NeedCtx,
) {
  const base = axisBounds(people);
  const bounds = demand?.length ? withOpenHours(base, ctx.staffing) : base;
  const points = occupancySeries(people, bounds.startMin, bounds.endMin, 15);
  const need = demand?.length
    ? applyDayRules(
        iso,
        points.map((p) => p.min),
        needSeries(iso, points, demand, ctx.staffing.ordersPerPerson, floorOf(ctx.staffing)),
        ctx.dayRules,
        ctx.deliveries,
      )
    : null;
  return { bounds, points, need };
}

/** Person-day hours, clocked when punched, else scheduled. */
function personDayHours(p: CoveragePersonDay): number {
  const actual = p.segments.filter((s) => s.kind === "actual");
  return (actual.length ? actual : p.segments).reduce((a, s) => a + s.hours, 0);
}

/** Draft shifts that add people/hours (not suggestions for existing ADP open shifts). */
function newShifts(shifts: DraftShift[]): DraftShift[] {
  return shifts.filter((s) => !s.fillsOpen);
}

function draftCountSeries(points: OccupancyPoint[], shifts: DraftShift[]): number[] {
  const added = newShifts(shifts);
  return points.map((p) => added.filter((s) => p.min >= s.startMin && p.min < s.endMin).length);
}

const GUTTER = "w-[7rem] sm:w-32";
const NO_OPEN: OpenShiftInput[] = [];
const NO_RULES_HISTORY: RulesVersion[] = [];
const NO_UNAVAILABILITY: UnavailabilityInput[] = [];

function chipLabel(iso: string): { weekday: string; monthDay: string } {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y!, m! - 1, d!);
  return {
    weekday: dt.toLocaleDateString("en-US", { weekday: "short" }),
    monthDay: dt.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
  };
}

function barColor(bucket: string, kind: CoverageKind): string {
  if (kind === "open") return OPEN;
  if (kind === "scheduled") return SCHED;
  return bucket === "fulltime" ? FT : PT;
}

function syncDayInUrl(day: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.set("day", day);
  window.history.replaceState(window.history.state, "", url.toString());
}

function DayStrip({
  chips,
  selected,
  onSelect,
  todayIso,
  shortHours,
  draftShifts,
}: {
  chips: CoverageDayChip[];
  selected: string;
  onSelect: (day: string) => void;
  todayIso: string;
  shortHours?: Map<string, number>;
  draftShifts?: Map<string, DraftShift[]>;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);

  // Keep the active chip in view when selection / Period changes.
  useEffect(() => {
    selectedRef.current?.scrollIntoView({
      behavior: "smooth",
      inline: "center",
      block: "nearest",
    });
  }, [selected, chips.length]);

  if (!chips.length) return null;

  return (
    <div className="relative">
      <div
        ref={scrollerRef}
        className={cn(
          "-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-2 pt-0.5",
          "scroll-smooth snap-x snap-mandatory",
          // Subtle edge fade so overflow reads as scrollable, not clipped.
          "[mask-image:linear-gradient(90deg,transparent,black_12px,black_calc(100%-12px),transparent)]",
          "sm:[mask-image:none]",
        )}
        role="listbox"
        aria-label="Coverage day"
      >
        {chips.map((chip) => {
          const active = chip.date === selected;
          const isToday = chip.date === todayIso;
          const label = chipLabel(chip.date);
          const suffix =
            chip.kind === "scheduled"
              ? "sched"
              : chip.kind === "empty"
                ? "—"
                : "people";
          return (
            <button
              key={chip.date}
              ref={active ? selectedRef : undefined}
              type="button"
              role="option"
              aria-selected={active}
              onClick={() => onSelect(chip.date)}
              className={cn(
                "flex min-h-12 w-[4.5rem] shrink-0 snap-start flex-col items-center justify-center rounded-xl border px-2 py-1.5 text-center transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active
                  ? "border-primary bg-primary/10 text-foreground shadow-sm"
                  : "border-border/80 bg-card/80 text-muted-foreground hover:border-border hover:bg-muted/50 hover:text-foreground",
                isToday && !active && "ring-1 ring-inset ring-primary/30",
              )}
            >
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {label.weekday}
                {isToday ? (
                  <span className="text-primary"> · Today</span>
                ) : null}
              </span>
              <span className="text-sm font-semibold tabular-nums text-foreground">
                {label.monthDay}
              </span>
              <Badge
                variant={active ? "default" : "secondary"}
                className="mt-1 h-4 max-w-full truncate px-1.5 text-[11px] font-normal"
              >
                {chip.headcount} {suffix}
              </Badge>
              {chip.open > 0 ? (
                <span
                  className="mt-0.5 text-[11px] font-medium leading-none"
                  style={{ color: OPEN }}
                >
                  +{chip.open} open
                </span>
              ) : null}
              {newShifts(draftShifts?.get(chip.date) ?? []).length ? (
                <span
                  className="mt-0.5 text-[11px] font-medium tabular-nums"
                  style={{ color: DRAFT_COLOR }}
                >
                  +{newShifts(draftShifts!.get(chip.date)!).length} draft ·{" "}
                  {newShifts(draftShifts!.get(chip.date)!).reduce((h, d) => h + d.hours, 0)}h
                </span>
              ) : (shortHours?.get(chip.date) ?? 0) > 0 ? (
                <span className="mt-0.5 text-[11px] font-medium tabular-nums text-rose-600 dark:text-rose-400">
                  {shortHours!.get(chip.date)!.toFixed(1)}h short
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      {chips.length > 7 ? (
        <p className="px-1 text-xs text-muted-foreground">
          {chips.length} days in range · scroll for more
        </p>
      ) : null}
    </div>
  );
}

function CoverageRibbonBars({
  points,
  axisStart,
  axisEnd,
  need,
  draft,
}: {
  points: OccupancyPoint[];
  axisStart: number;
  axisEnd: number;
  need: number[] | null;
  draft: number[] | null;
}) {
  const maxH = Math.max(
    1,
    ...points.map((p, i) => Math.max(p.actual, p.scheduled) + p.open + (draft?.[i] ?? 0)),
    ...(need ?? []),
  );
  const span = axisEnd - axisStart;
  const bucketPct = (15 / span) * 100;
  // Thin stems (~30% of bucket, capped) so height reads clearly without a brick wall.
  const barPct = Math.min(0.55, Math.max(0.25, bucketPct * 0.28));

  return (
    <div className="relative h-14 w-full">
      {need
        ? points.map((p, i) => {
            const n = need[i] ?? 0;
            if (n <= 0) return null;
            const short = onFloor(p) + (draft?.[i] ?? 0) < n;
            return (
              <div
                key={`need-${p.min}`}
                aria-hidden
                className="absolute bottom-0"
                style={{
                  left: `${((p.min - axisStart) / span) * 100}%`,
                  width: `${bucketPct}%`,
                  height: `${(n / maxH) * 100}%`,
                  borderTop: `1.5px solid ${NEED}`,
                  backgroundColor: short ? SHORT_TINT : undefined,
                }}
              />
            );
          })
        : null}
      {points.map((p, i) => {
        const bucketLeft = ((p.min - axisStart) / span) * 100;
        const left = bucketLeft + (bucketPct - barPct) / 2;
        const aH = (p.actual / maxH) * 100;
        const sH = (p.scheduled / maxH) * 100;
        const oH = (p.open / maxH) * 100;
        const dH = ((draft?.[i] ?? 0) / maxH) * 100;
        return (
          <div
            key={p.min}
            className="absolute bottom-0 flex flex-col justify-end gap-px"
            style={{ left: `${left}%`, width: `${barPct}%`, height: "100%" }}
          >
            {dH > 0 ? (
              <div
                className="mx-auto w-full max-w-[2.5px] rounded-[1px]"
                style={{ height: `${dH}%`, backgroundColor: DRAFT_COLOR }}
              />
            ) : null}
            {p.open > 0 ? (
              <div
                className="mx-auto w-full max-w-[2.5px] rounded-[1px] border border-dashed"
                style={{ height: `${oH}%`, borderColor: OPEN, background: OPEN_HATCH }}
              />
            ) : null}
            {p.scheduled > 0 ? (
              <div
                className="mx-auto w-full max-w-[2.5px] rounded-[1px] opacity-80"
                style={{
                  height: `${sH}%`,
                  background: `repeating-linear-gradient(
                    -45deg,
                    ${SCHED},
                    ${SCHED} 1px,
                    transparent 1px,
                    transparent 3px
                  )`,
                  border: `1px solid ${SCHED}`,
                }}
              />
            ) : null}
            {p.actual > 0 ? (
              <div
                className="mx-auto w-full max-w-[2.5px] rounded-[1px]"
                style={{
                  height: `${aH}%`,
                  backgroundColor: PT,
                  opacity: 0.9,
                }}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function AxisTicks({ axisStart, axisEnd }: { axisStart: number; axisEnd: number }) {
  const ticks: number[] = [];
  for (let t = axisStart; t <= axisEnd; t += 60) ticks.push(t);
  return (
    <div className="flex justify-between pt-1 text-[11px] text-muted-foreground">
      {ticks.map((t) => (
        <span key={t}>{formatClockMin(t)}</span>
      ))}
    </div>
  );
}

function PersonLaneTrack({
  person,
  axisStart,
  axisSpan,
}: {
  person: CoveragePersonDay;
  axisStart: number;
  axisSpan: number;
}) {
  return (
    <div
      className="relative h-9 w-full shrink-0 overflow-hidden rounded-md bg-muted/40"
      data-lane={person.employee}
    >
      {person.segments.map((seg, i) => {
        const left = segmentLeftPct(seg, axisStart, axisSpan);
        const width = segmentWidthPct(seg, axisStart, axisSpan);
        const color = barColor(person.labor_bucket, seg.kind);
        const isSched = seg.kind === "scheduled";
        if (seg.kind === "open") {
          return (
            <div
              key={`open-${seg.startMin}-${i}`}
              className="absolute inset-y-1.5 flex items-center justify-center overflow-hidden rounded-sm border border-dashed text-[11px] font-semibold"
              style={{
                left: `${left}%`,
                width: `${Math.max(width, 0.8)}%`,
                borderColor: OPEN,
                color: OPEN,
                background: OPEN_HATCH,
              }}
            >
              {width > 8 ? `${seg.hours}h` : null}
            </div>
          );
        }
        return (
          <div
            key={`${seg.kind}-${seg.startMin}-${i}`}
            className={cn(
              "absolute flex items-center justify-center overflow-hidden rounded-sm text-[11px] font-medium text-white",
              isSched ? "top-[18px] h-3 border border-dashed opacity-90" : "top-1.5 h-3.5",
            )}
            style={{
              left: `${left}%`,
              width: `${Math.max(width, 0.8)}%`,
              backgroundColor: isSched ? "transparent" : color,
              borderColor: isSched ? color : undefined,
              backgroundImage: isSched
                ? `repeating-linear-gradient(-45deg, ${color}33, ${color}33 2px, transparent 2px, transparent 4px)`
                : undefined,
            }}
          >
            {width > 8 ? `${seg.hours}h` : null}
          </div>
        );
      })}
    </div>
  );
}

function CoverageTimeline({
  people,
  points,
  axisStart,
  axisEnd,
  scheduled,
  activeDay,
  todayIso,
  need,
  draftShifts,
}: {
  people: CoveragePersonDay[];
  points: OccupancyPoint[];
  axisStart: number;
  axisEnd: number;
  scheduled: ScheduledShiftInput[];
  activeDay: string;
  todayIso: string;
  need: number[] | null;
  draftShifts: DraftShift[];
}) {
  const draft = useMemo(
    () => (draftShifts.length ? draftCountSeries(points, draftShifts) : null),
    [points, draftShifts],
  );
  const trackRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ minute: number; pct: number } | null>(null);
  const span = axisEnd - axisStart;

  const onMove = useCallback(
    (e: React.MouseEvent) => {
      const el = trackRef.current;
      if (!el || !(span > 0)) return;
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0) return;
      const pct01 = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
      const minute = snapMinute(axisStart + pct01 * span, axisStart, 15);
      const pct = ((minute - axisStart) / span) * 100;
      setHover({ minute, pct });
    },
    [axisStart, span],
  );

  const active = hover ? peopleActiveAt(people, hover.minute) : [];
  const occ = hover
    ? (points.find((p) => p.min === hover.minute) ?? {
        actual: 0,
        scheduled: 0,
        open: 0,
        min: hover.minute,
      })
    : null;
  const shownCount = occ ? (occ.actual > 0 ? occ.actual : occ.scheduled) : 0;
  const openCount = occ?.open ?? 0;
  const hoverIdx = hover ? points.findIndex((p) => p.min === hover.minute) : -1;
  const neededCount = need && hoverIdx >= 0 ? (need[hoverIdx] ?? 0) : null;
  const draftAt = draft && hoverIdx >= 0 ? (draft[hoverIdx] ?? 0) : 0;
  const activeDraft = hover
    ? newShifts(draftShifts).filter((s) => hover.minute >= s.startMin && hover.minute < s.endMin)
    : [];

  const crosshair = hover ? (
    <div
      className="pointer-events-none absolute inset-y-0 z-10 w-px bg-foreground/60"
      style={{ left: `${hover.pct}%` }}
      aria-hidden
    />
  ) : null;

  const tooltip = hover ? (
    <div
      className="pointer-events-none absolute top-1 z-20 w-64 max-w-[min(16rem,100%)]"
      style={{
        left: `clamp(0px, calc(${hover.pct}% - 8rem), calc(100% - 16rem))`,
      }}
    >
      <div
        className="rounded-md border border-border px-2.5 py-2 text-xs shadow-md"
        style={{
          background: "var(--popover)",
          color: "var(--popover-foreground)",
        }}
      >
        <p className="mb-1.5 font-medium">
          {formatClockMin(hover.minute)} · {shownCount} on floor
          {openCount > 0 ? (
            <span style={{ color: OPEN }}> · {openCount} open</span>
          ) : null}
          {neededCount ? (
            <span
              className={cn(
                "font-normal",
                shownCount + openCount + draftAt < neededCount
                  ? "text-rose-600 dark:text-rose-400"
                  : "text-muted-foreground",
              )}
            >
              {" "}
              · {neededCount} needed
            </span>
          ) : null}
          {draftAt ? (
            <span className="font-normal" style={{ color: DRAFT_COLOR }}>
              {" "}
              · +{draftAt} draft
            </span>
          ) : null}
        </p>
        {activeDraft.length ? (
          <ul className="mb-1 flex flex-col gap-1">
            {activeDraft.map((s, i) => (
              <li
                key={`draft-${s.startMin}-${i}`}
                className="flex items-start justify-between gap-3"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span
                    className="mt-0.5 inline-block size-2.5 shrink-0 rounded-sm"
                    style={{ backgroundColor: DRAFT_COLOR }}
                  />
                  <span className="truncate">{s.employee ?? "Open shift"}</span>
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {formatClockMin(s.startMin)}–{formatClockMin(s.endMin)} · {s.hours}h
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {active.length ? (
          <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto">
            {active.map((p) => (
              <li
                key={`${p.employee}-${p.kind}`}
                className="flex items-start justify-between gap-3"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span
                    className={cn(
                      "mt-0.5 inline-block size-2.5 shrink-0 rounded-sm",
                      p.kind === "open" && "border border-dashed",
                    )}
                    style={
                      p.kind === "open"
                        ? { borderColor: OPEN, background: OPEN_HATCH }
                        : {
                            backgroundColor: barColor(p.labor_bucket, p.kind),
                            opacity: p.kind === "scheduled" ? 0.7 : 1,
                          }
                    }
                  />
                  <span className={cn("truncate", p.kind === "open" && "italic")}>
                    {p.kind === "open" ? "Open (unassigned)" : p.employee}
                  </span>
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {formatClockMin(p.startMin)}–{formatClockMin(p.endMin)}
                </span>
              </li>
            ))}
          </ul>
        ) : activeDraft.length ? null : (
          <p className="text-muted-foreground">Nobody on at this time.</p>
        )}
      </div>
    </div>
  ) : null;

  return (
    <div
      className="flex flex-col gap-3"
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      {/* Headcount ribbon — label | track */}
      <div className="flex items-end gap-2">
        <div
          className={cn(
            GUTTER,
            "shrink-0 pb-5 text-[11px] leading-none text-muted-foreground",
          )}
        >
          Headcount
        </div>
        <div ref={trackRef} className="relative min-w-0 flex-1 cursor-crosshair">
          <div className="rounded-md border border-border bg-muted/30 px-1 pt-2 pb-1">
            <CoverageRibbonBars
              points={points}
              axisStart={axisStart}
              axisEnd={axisEnd}
              need={need}
              draft={draft}
            />
            <AxisTicks axisStart={axisStart} axisEnd={axisEnd} />
          </div>
          {crosshair}
          {tooltip}
        </div>
      </div>

      {/* One flex row per person — name + bar share the same row (can't overlap). */}
      <div className="relative flex flex-col gap-2">
        {people.length ? (
          people.map((p) => {
            const totalHrs = p.segments.reduce((sum, s) => sum + s.hours, 0);
            const open = isOpenLane(p);
            const takers = open
              ? draftShifts
                  .filter((s) => s.fillsOpen && p.segments.some((g) => g.startMin === s.startMin && g.endMin === s.endMin))
                  .map((s) => s.employee)
              : [];
            return (
              <div key={p.employee} className="flex items-center gap-2">
                <div className={cn(GUTTER, "shrink-0 truncate")}>
                  <span
                    className={cn("block truncate text-sm font-medium", open && "italic")}
                    style={open ? { color: OPEN } : undefined}
                    title={p.employee}
                  >
                    {p.employee}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {totalHrs.toFixed(1)}h
                    {open ? (activeDay < todayIso ? " · unfilled" : " · unassigned") : ""}
                  </span>
                  {takers.length ? (
                    <span
                      className="block truncate text-xs"
                      style={{ color: DRAFT_COLOR }}
                      title={`Suggested: ${takers.join(", ")}`}
                    >
                      suggest {takers.join(", ")}
                    </span>
                  ) : null}
                </div>
                <div className="relative min-w-0 flex-1 cursor-crosshair">
                  <PersonLaneTrack
                    person={p}
                    axisStart={axisStart}
                    axisSpan={span}
                  />
                </div>
              </div>
            );
          })
        ) : draftShifts.length ? null : (
          <p className="text-sm text-muted-foreground">
            No clocked ADP punches for this day
            {scheduled.some((s) => s.date.slice(0, 10) === activeDay)
              ? " — Team Schedule had no usable shift ranges to draw."
              : " — if the store was open, Timecard may not have included this date yet."}
          </p>
        )}
        {newShifts(draftShifts).map((s, i) => {
          const left = ((s.startMin - axisStart) / span) * 100;
          const width = ((s.endMin - s.startMin) / span) * 100;
          return (
            <div key={`draft-${s.startMin}-${i}`} className="flex items-center gap-2">
              <div className={cn(GUTTER, "shrink-0 truncate")}>
                <span
                  className={cn(
                    "block truncate text-sm font-medium",
                    !s.employee && "italic text-muted-foreground",
                  )}
                  title={s.employee ?? "Open shift"}
                >
                  {s.employee ?? "Open shift"}
                </span>
                <span className="text-xs" style={{ color: DRAFT_COLOR }}>
                  draft · {s.hours.toFixed(1)}h
                </span>
              </div>
              <div className="relative min-w-0 flex-1 cursor-crosshair">
                <div className="relative h-9 w-full overflow-hidden rounded-md bg-muted/40">
                  <div
                    className="absolute top-1.5 flex h-6 items-center justify-center overflow-hidden rounded-sm border border-dashed text-[11px] font-medium"
                    style={{
                      left: `${left}%`,
                      width: `${Math.max(width, 0.8)}%`,
                      borderColor: DRAFT_COLOR,
                      backgroundColor: `${DRAFT_COLOR}26`,
                      color: DRAFT_COLOR,
                    }}
                  >
                    {width > 14
                      ? `${s.kind === "open" ? "Open" : s.kind === "mid" ? "Mid" : "Close"} · ${formatClockMin(s.startMin)}–${formatClockMin(s.endMin)} · ${s.hours}h`
                      : `${s.hours}h`}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
        {/* Crosshair over track column only (past name gutter + gap). */}
        {hover ? (
          <div
            className="pointer-events-none absolute inset-y-0 left-[calc(7rem+0.5rem)] right-0 z-10 sm:left-[calc(8rem+0.5rem)]"
            aria-hidden
          >
            <div
              className="absolute inset-y-0 w-px bg-foreground/60"
              style={{ left: `${hover.pct}%` }}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function LaborCoveragePanel({
  win,
  actuals,
  scheduled,
  open = NO_OPEN,
  laborTypes,
  todayIso = chicagoTodayIso(),
  demand,
  goalHoursWeek,
  deliveryDates,
  rulesHistory = NO_RULES_HISTORY,
  unavailability = NO_UNAVAILABILITY,
  activeStaff,
  adpWriteEnabled = false,
}: {
  win: DateWindow;
  actuals: ActualShiftInput[];
  scheduled: ScheduledShiftInput[];
  /** ADP open (unassigned) slots — shown on past days too, as unfilled gaps. */
  open?: OpenShiftInput[];
  laborTypes: string[] | null;
  todayIso?: string;
  /** Median orders per weekday × hour (last 8 weeks); enables the Needed line. */
  demand?: DemandCell[];
  goalHoursWeek?: number;
  /** Upcoming frozen delivery dates (restock schedule). */
  deliveryDates?: string[];
  /** Saved scheduling-rules versions, newest (live) first. */
  rulesHistory?: RulesVersion[];
  /** ADP unavailability (pending + approved); the draft works around it. */
  unavailability?: UnavailabilityInput[];
  /** People on the ADP schedule ahead — the only ones the draft suggests (drops departed staff). */
  activeStaff?: string[];
  /** FEATURES.adpScheduleWrite, evaluated on the server. */
  adpWriteEnabled?: boolean;
}) {
  const strip = useMemo(
    () => coverageStripDates(win),
    [win.start, win.end],
  );
  const initial = useMemo(
    () => defaultCoverageDay(strip, todayIso),
    [strip, todayIso],
  );
  const [day, setDay] = useState<string | null>(initial);
  const dayKey = `${initial ?? ""}|${strip.join(",")}`;
  const [prevKey, setPrevKey] = useState(dayKey);
  if (dayKey !== prevKey) {
    setPrevKey(dayKey);
    setDay(initial);
  }

  const scheduledForCoverage = useMemo(
    () => filterScheduledForCoverage(actuals, scheduled, todayIso),
    [actuals, scheduled, todayIso],
  );

  // Open lanes lead so unfilled capacity is the first thing read for a day.
  const lanesFor = useCallback(
    (iso: string) => [
      ...buildOpenLanesForDate(iso, open),
      ...buildPersonDaysForDate(iso, actuals, scheduledForCoverage, laborTypes),
    ],
    [open, actuals, scheduledForCoverage, laborTypes],
  );

  const baseRoster = useMemo(() => {
    const hours = new Map<string, number>();
    for (const r of actuals)
      hours.set(r.employee, (hours.get(r.employee) ?? 0) + (r.total_hours || 0));
    for (const r of scheduled) {
      hours.set(r.employee, (hours.get(r.employee) ?? 0) + (r.scheduled_hours || 0));
    }
    if (activeStaff) {
      const active = new Set(activeStaff);
      for (const name of hours.keys()) if (!active.has(name)) hours.delete(name);
      for (const name of active) if (!hours.has(name)) hours.set(name, 0);
    }
    return adpRoster(hours);
  }, [actuals, scheduled, activeStaff]);

  const saved = rulesHistory[0];
  const savedVersion = saved?.version ?? 0;
  const savedRules = saved?.rules ?? DEFAULT_RULES;
  // Unsaved edits preview in the draft immediately; null = showing the saved version.
  const [editedRules, setEditedRules] = useState<ScheduleRules | null>(null);
  const [rulesBase, setRulesBase] = useState(savedVersion);
  if (rulesBase !== savedVersion) {
    setRulesBase(savedVersion);
    setEditedRules(null);
  }
  const rules = editedRules ?? savedRules;
  const inputs = useMemo<NeedCtx>(
    () => ({ staffing: rules.staffing, dayRules: rules.dayRules, deliveries: new Set(deliveryDates ?? []) }),
    [rules.staffing, rules.dayRules, deliveryDates],
  );
  const roster = useMemo(() => {
    const limits = staffLimits(rules.staffRules);
    return baseRoster.map((a) => ({ ...a, maxWeekHours: rules.staffing.maxWeekHours, ...limits.get(a.employee) }));
  }, [baseRoster, rules.staffRules, rules.staffing.maxWeekHours]);

  const { chips, shortHours } = useMemo(() => {
    const short = new Map<string, number>();
    const summaries = strip.map((iso) => {
      const lanes = lanesFor(iso);
      if (iso >= todayIso) {
        const cov = dayCoverage(iso, lanes, demand, inputs);
        if (cov.need) short.set(iso, shortPersonHours(cov.points, cov.need));
      }
      return dayChipSummary(iso, lanes);
    });
    return { chips: summaries, shortHours: short };
  }, [strip, lanesFor, demand, inputs, todayIso]);

  const activeDay = day ?? initial;

  const people = useMemo(
    () => (activeDay ? lanesFor(activeDay) : []),
    [activeDay, lanesFor],
  );

  const { bounds, points, need } = useMemo(
    () =>
      dayCoverage(
        activeDay ?? "",
        people,
        activeDay && activeDay >= todayIso ? demand : undefined,
        inputs,
      ),
    [activeDay, people, demand, inputs, todayIso],
  );
  const narrative = useMemo(() => coverageNarrative(points), [points]);
  const openSummary = useMemo(() => {
    const segs = people.filter(isOpenLane).flatMap((p) => p.segments);
    if (!segs.length || !activeDay) return null;
    const hrs = segs.reduce((sum, s) => sum + s.hours, 0);
    const noun = segs.length === 1 ? "open shift" : "open shifts";
    const state = activeDay < todayIso ? "went unfilled" : "unassigned";
    return `${segs.length} ${noun} (${Number(hrs.toFixed(1))}h) ${state}`;
  }, [people, activeDay, todayIso]);

  const [showDraft, setShowDraft] = useState(true);

  // Draft each Mon–Sun week from tomorrow through the strip horizon (today's
  // shifts are already under way). Each week
  // has its own hours budget; weekly hours are shared within a week so hour
  // targets and caps span it. Shift counts carry across weeks per pay period.
  const buildDrafts = useCallback((r: ScheduleRules) => {
    if (!demand?.length) return null;
    const inputs: NeedCtx = {
      staffing: r.staffing,
      dayRules: r.dayRules,
      deliveries: new Set(deliveryDates ?? []),
    };
    const limits = staffLimits(r.staffRules);
    const roster = baseRoster.map((a) => ({ ...a, maxWeekHours: r.staffing.maxWeekHours, ...limits.get(a.employee) }));
    const weekStarts = [
      ...new Set(
        strip
          .filter((iso) => iso > todayIso)
          .map((iso) => shiftCalendarDate(iso, "day", -isoWeekdayMon0(iso))),
      ),
    ].sort();
    const periodShifts = new Map<string, Map<string, number>>();
    const shiftsInPeriod = (iso: string) => {
      const start = payPeriodStartFor(iso);
      let counts = periodShifts.get(start);
      if (!counts) {
        counts = new Map();
        for (let i = 0; i < 14; i++) {
          const d = shiftCalendarDate(start, "day", i);
          for (const p of buildPersonDaysForDate(d, actuals, scheduledForCoverage, laborTypes))
            counts.set(p.employee, (counts.get(p.employee) ?? 0) + 1);
        }
        periodShifts.set(start, counts);
      }
      return counts;
    };
    const draftWeek = (weekStart: string) => {
      const days = Array.from({ length: 7 }, (_, i) => shiftCalendarDate(weekStart, "day", i));
      const peopleByDay = new Map(days.map((iso) => [iso, lanesFor(iso)]));
      const weekHours = new Map<string, number>();
      let existingHours = 0;
      for (const dayPeople of peopleByDay.values()) {
        for (const p of dayPeople) {
          const h = personDayHours(p);
          existingHours += h;
          if (!isOpenLane(p)) weekHours.set(p.employee, (weekHours.get(p.employee) ?? 0) + h);
        }
      }
      const state = days
        .filter((iso) => iso > todayIso)
        .map((iso) => {
          const dayPeople = peopleByDay.get(iso)!;
          const cov = dayCoverage(iso, dayPeople, demand, inputs);
          const mins = cov.points.map((p) => p.min);
          const floorOnly = needSeries(iso, cov.points, [], inputs.staffing.ordersPerPerson, floorOf(inputs.staffing));
          return {
            iso,
            mins,
            cover: cov.points.map(onFloor),
            floorOnly,
            floorNeed: applyDayRules(iso, mins, floorOnly, inputs.dayRules, inputs.deliveries),
            need: cov.need!,
            busy: new Set(dayPeople.filter((p) => !isOpenLane(p)).map((p) => p.employee)),
            shifts: [] as DraftShift[],
          };
        });
      const add = (d: (typeof state)[number], shifts: DraftShift[]) => {
        for (const s of shifts) {
          d.shifts.push(s);
          if (s.employee) d.busy.add(s.employee);
          d.mins.forEach((t, i) => {
            if (t >= s.startMin && t < s.endMin) d.cover[i]! += 1;
          });
        }
      };
      const run = (d: (typeof state)[number], need: number[], maxShifts?: number) =>
        draftDay({
          iso: d.iso,
          mins: d.mins,
          onFloor: d.cover,
          need,
          roster,
          weekHours,
          busy: d.busy,
          periodShifts: shiftsInPeriod(d.iso),
          unavailable: blocksOn(unavailability, d.iso),
          minShiftMin: r.staffing.minShiftMin,
          maxShiftMin: r.staffing.maxShiftMin,
          shiftTimes: r.staffing.shiftTimes,
          handoverOverlapMin: r.staffing.handoverOverlapMin,
          maxShifts,
        });

      // ADP open shifts already count as coverage and hours; only suggest who takes them.
      for (const d of state) {
        const slots = peopleByDay.get(d.iso)!.filter(isOpenLane).flatMap((p) => p.segments);
        for (const slot of slots) {
          const fill = fillOpenShift({
            iso: d.iso,
            startMin: slot.startMin,
            endMin: slot.endMin,
            roster,
            weekHours,
            busy: d.busy,
            periodShifts: shiftsInPeriod(d.iso),
            unavailable: blocksOn(unavailability, d.iso),
          });
          if (fill) d.shifts.push(fill);
        }
      }
      // Pass 1: labor floor + day rules are mandatory regardless of the goal.
      const breakdown = emptyBreakdown(existingHours, r.dayRules.map((x) => x.id));
      for (const d of state) {
        const split = requiredByRule({
          iso: d.iso,
          mins: d.mins,
          floorOnly: d.floorOnly,
          cover: [...d.cover],
          dayRules: r.dayRules,
          deliveries: inputs.deliveries,
        });
        const drafted = run(d, d.floorNeed);
        add(d, drafted);
        addDay(breakdown, split, drafted.reduce((a, s) => a + s.hours, 0));
      }
      // Pass 2: spend what's left of the goal on the largest order-driven gaps.
      let budget =
        goalHoursWeek != null
          ? goalHoursWeek -
            existingHours -
            state.reduce((a, d) => a + newShifts(d.shifts).reduce((b, s) => b + s.hours, 0), 0)
          : Infinity;
      let peakLeftHours = 0;
      for (;;) {
        const gaps = state
          .map((d) => ({
            d,
            short: d.need.reduce((a, n, i) => a + Math.max(0, n - d.cover[i]!) * 0.25, 0),
          }))
          .filter((g) => g.short > 0)
          .sort((a, b) => b.short - a.short);
        if (!gaps.length) break;
        // Biggest gap first; if its shift doesn't fit the goal, a smaller gap's might.
        let placed = false;
        for (const g of gaps) {
          const next = run(g.d, g.d.need, 1)[0];
          if (next && next.hours <= budget) {
            add(g.d, [next]);
            budget -= next.hours;
            breakdown.peak += next.hours;
            placed = true;
            break;
          }
          if (next?.employee) {
            weekHours.set(next.employee, (weekHours.get(next.employee) ?? 0) - next.hours);
            const counts = shiftsInPeriod(next.date);
            counts.set(next.employee, (counts.get(next.employee) ?? 1) - 1);
          }
        }
        if (!placed) {
          peakLeftHours = gaps.reduce((a, g) => a + g.short, 0);
          break;
        }
      }

      const byDay = new Map<string, DraftShift[]>();
      for (const d of state) {
        if (d.shifts.length)
          byDay.set(
            d.iso,
            [...d.shifts].sort((a, b) => a.startMin - b.startMin),
          );
      }
      const all = newShifts([...byDay.values()].flat());
      return {
        weekStart,
        byDay,
        existingHours,
        draftHours: all.reduce((a, s) => a + s.hours, 0),
        draftCount: all.length,
        peakLeftHours,
        breakdown,
      };
    };
    const weeks = new Map(weekStarts.map((w) => [w, draftWeek(w)]));
    const byDay = new Map<string, DraftShift[]>();
    for (const w of weeks.values()) for (const [iso, s] of w.byDay) byDay.set(iso, s);
    return { weeks, byDay };
  }, [
    demand,
    strip,
    todayIso,
    lanesFor,
    actuals,
    scheduledForCoverage,
    laborTypes,
    deliveryDates,
    baseRoster,
    goalHoursWeek,
    unavailability,
  ]);
  const drafts = useMemo(() => buildDrafts(rules), [buildDrafts, rules]);
  const savedDrafts = useMemo(
    () => (editedRules ? buildDrafts(savedRules) : drafts),
    [buildDrafts, editedRules, savedRules, drafts],
  );
  const weekForecast = useMemo(
    () =>
      [...(drafts?.weeks.values() ?? [])].map((w) => {
        const before = savedDrafts?.weeks.get(w.weekStart);
        return {
          weekStart: w.weekStart,
          before: before ? before.existingHours + before.draftHours : null,
          after: w.existingHours + w.draftHours,
        };
      }),
    [drafts, savedDrafts],
  );

  const { publish: publishSuggested } = useSuggestedHours();
  const staffedHours = Math.max(1, (rules.staffing.closeMin - rules.staffing.openMin) / 60);
  useEffect(() => {
    const byDay = new Map<string, number>();
    const concurrent = new Map<string, number>();
    const perPerson = new Map<string, Map<string, number>>();
    if (showDraft && drafts) {
      const inStrip = new Set(strip);
      for (const [iso, shifts] of drafts.byDay) {
        if (!inStrip.has(iso)) continue;
        const added = newShifts(shifts);
        const h = added.reduce((a, s) => a + s.hours, 0);
        if (h > 0) {
          byDay.set(iso, h);
          concurrent.set(iso, h / staffedHours);
          const people = new Map<string, number>();
          for (const s of added) people.set(s.employee ?? "", (people.get(s.employee ?? "") ?? 0) + s.hours);
          perPerson.set(iso, people);
        }
      }
    }
    publishSuggested(byDay, concurrent, perPerson);
  }, [showDraft, drafts, strip, staffedHours, publishSuggested]);
  useEffect(() => () => publishSuggested(new Map(), new Map(), new Map()), [publishSuggested]);

  const { publishOpen } = useSuggestedHours();
  useEffect(() => {
    const rows: OpenShift[] = [];
    for (const iso of strip) {
      if (iso <= todayIso) continue;
      const day = drafts?.byDay.get(iso) ?? [];
      for (const seg of lanesFor(iso).filter(isOpenLane).flatMap((p) => p.segments)) {
        const taker = day.find((s) => s.fillsOpen && s.startMin === seg.startMin && s.endMin === seg.endMin);
        rows.push({ date: iso, startMin: seg.startMin, endMin: seg.endMin, hours: seg.hours, source: "adp", suggested: taker?.employee ?? null });
      }
      if (!showDraft) continue;
      for (const s of newShifts(day)) {
        if (!s.employee) rows.push({ date: iso, startMin: s.startMin, endMin: s.endMin, hours: s.hours, source: "draft" });
      }
    }
    publishOpen(rows, rules.staffing.maxWeekHours);
  }, [strip, todayIso, drafts, lanesFor, showDraft, rules.staffing.maxWeekHours, publishOpen]);

  const weekDraft =
    drafts && activeDay && activeDay > todayIso
      ? (drafts.weeks.get(shiftCalendarDate(activeDay, "day", -isoWeekdayMon0(activeDay))) ?? null)
      : null;

  const draftShifts = useMemo(
    () => (showDraft && activeDay ? (weekDraft?.byDay.get(activeDay) ?? []) : []),
    [showDraft, activeDay, weekDraft],
  );
  const weekShifts = useMemo(
    () =>
      newShifts([...(weekDraft?.byDay.values() ?? [])].flat())
        .map(({ date, employee, startMin, endMin }) => ({ date, employee, startMin, endMin })),
    [weekDraft],
  );

  const shortLine = useMemo(() => {
    if (!need) return null;
    const draft = draftCountSeries(points, draftShifts);
    const withDraft = points.map((p, i) => ({
      ...p,
      actual: 0,
      open: 0,
      scheduled: onFloor(p) + draft[i]!,
    }));
    const line = shortNarrative(shortWindows(withDraft, need));
    return newShifts(draftShifts).length ? `With draft — ${line}` : line;
  }, [points, need, draftShifts]);

  if (!activeDay) return null;

  const label = chipLabel(activeDay);

  return (
    <Card>
      <CardHeader className="gap-1">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Staffing coverage
        </CardTitle>
        <CardDescription>
          Day chips follow the Period (and, when today is in range, through the latest
          ADP schedule — same horizon as the charts above). Scheduled swimlanes always
          render when ADP has shifts in that window — Aggregation does not filter them.
          Scroll for more days; chips update this panel in place. Hover the timeline for
          headcount + who is on (start–end). Solid = clocked; slate hatch = scheduled;
          violet dashed = open (unassigned) ADP shift — on past days, a slot nobody filled.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {demand?.length ? (
          <ScheduleInputsPanel
            rules={rules}
            onChange={setEditedRules}
            dirty={editedRules != null}
            onDiscard={() => setEditedRules(null)}
            savedVersion={savedVersion}
            history={rulesHistory}
            employees={baseRoster.map((a) => a.employee).sort()}
            deliveryDates={deliveryDates ?? []}
            goalHoursWeek={goalHoursWeek}
            forecast={weekForecast}
          />
        ) : null}

        <DayStrip
          chips={chips}
          selected={activeDay}
          todayIso={todayIso}
          shortHours={demand?.length ? shortHours : undefined}
          draftShifts={showDraft ? drafts?.byDay : undefined}
          onSelect={(next) => {
            setDay(next);
            syncDayInUrl(next);
          }}
        />

        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-medium text-foreground">
              Coverage — {label.weekday} {label.monthDay}
            </h3>
            {weekDraft ? (
              <Button
                size="sm"
                variant={showDraft ? "default" : "outline"}
                aria-pressed={showDraft}
                onClick={() => setShowDraft((v) => !v)}
              >
                {showDraft ? "Hide draft shifts" : "Draft shifts"}
              </Button>
            ) : null}
          </div>
          <p className="text-sm text-muted-foreground">
            {narrative}
            {openSummary ? (
              <span style={{ color: OPEN }}> · {openSummary}</span>
            ) : null}
          </p>
          {shortLine ? (
            <p
              data-testid="coverage-short"
              className={cn(
                "text-sm",
                /short:/i.test(shortLine)
                  ? "text-rose-600 dark:text-rose-400"
                  : "text-muted-foreground",
              )}
            >
              {shortLine}
            </p>
          ) : null}
        </div>

        {showDraft && weekDraft ? (
          <ShiftDraftSummary
            dayLabel={`${label.weekday} ${label.monthDay}`}
            shifts={draftShifts}
            roster={roster}
            weekStart={weekDraft.weekStart}
            existingHours={weekDraft.existingHours}
            draftHours={weekDraft.draftHours}
            draftCount={weekDraft.draftCount}
            peakLeftHours={weekDraft.peakLeftHours}
            breakdown={weekDraft.breakdown}
            dayRules={rules.dayRules}
            goalHoursWeek={goalHoursWeek}
            weekShifts={weekShifts}
            adpWriteEnabled={adpWriteEnabled}
          />
        ) : null}

        <CoverageTimeline
          people={people}
          points={points}
          axisStart={bounds.startMin}
          axisEnd={bounds.endMin}
          scheduled={scheduledForCoverage}
          activeDay={activeDay}
          todayIso={todayIso}
          need={need}
          draftShifts={draftShifts}
        />

        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: PT }} />
            Actual PT
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: FT }} />
            Actual FT
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block size-2.5 rounded-sm border border-dashed"
              style={{ borderColor: SCHED }}
            />
            Scheduled
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block size-2.5 rounded-sm border border-dashed"
              style={{ borderColor: OPEN, background: OPEN_HATCH }}
            />
            Open (unassigned)
          </span>
          {need ? (
            <>
              <span
                className="inline-flex items-center gap-1.5"
                title={`Larger of the minimum (${rules.staffing.minPeople}) and typical (median) orders for this weekday+hour over the last 8 weeks ÷ ${rules.staffing.ordersPerPerson} per person, rounded up, then your day & time rules`}
              >
                <span
                  className="inline-block h-0.5 w-3 rounded-full"
                  style={{ backgroundColor: NEED }}
                />
                Needed
              </span>
              <span className="inline-flex items-center gap-1.5">
                <span
                  className="inline-block size-2.5 rounded-sm"
                  style={{ backgroundColor: SHORT_TINT }}
                />
                Short
              </span>
            </>
          ) : null}
          {newShifts(draftShifts).length ? (
            <span className="inline-flex items-center gap-1.5">
              <span
                className="inline-block size-2.5 rounded-sm border border-dashed"
                style={{
                  borderColor: DRAFT_COLOR,
                  backgroundColor: `${DRAFT_COLOR}26`,
                }}
              />
              Draft
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
