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
  buildPersonDaysForDate,
  coverageNarrative,
  coverageStripDates,
  dayChipSummary,
  formatClockMin,
  occupancySeries,
  peopleActiveAt,
  resolveCoverageDay,
  segmentLeftPct,
  segmentWidthPct,
  snapMinute,
  type ActualShiftInput,
  type CoverageDayChip,
  type CoveragePersonDay,
  type OccupancyPoint,
  type ScheduledShiftInput,
} from "@/lib/labor/coverage-model";
import { chicagoTodayIso, type DateWindow } from "@/lib/filters/range";
import { cn } from "@/lib/utils";

const PT = LABOR_CHART_COLORS.parttimeActual;
const FT = LABOR_CHART_COLORS.fulltimeActual;
const SCHED = LABOR_CHART_COLORS.parttimeScheduled;

const GUTTER = "w-[7rem] sm:w-32";

function chipLabel(iso: string): { weekday: string; monthDay: string } {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y!, m! - 1, d!);
  return {
    weekday: dt.toLocaleDateString("en-US", { weekday: "short" }),
    monthDay: dt.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
  };
}

function barColor(bucket: string, kind: "actual" | "scheduled"): string {
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
}: {
  chips: CoverageDayChip[];
  selected: string;
  onSelect: (day: string) => void;
  todayIso: string;
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
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
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
                className="mt-1 h-4 max-w-full truncate px-1.5 text-[10px] font-normal"
              >
                {chip.headcount} {suffix}
              </Badge>
            </button>
          );
        })}
      </div>
      {chips.length > 7 ? (
        <p className="px-1 text-[11px] text-muted-foreground">
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
}: {
  points: OccupancyPoint[];
  axisStart: number;
  axisEnd: number;
}) {
  const maxH = Math.max(1, ...points.map((p) => Math.max(p.actual, p.scheduled)));
  const span = axisEnd - axisStart;
  const bucketPct = (15 / span) * 100;
  // Thin stems (~30% of bucket, capped) so height reads clearly without a brick wall.
  const barPct = Math.min(0.55, Math.max(0.25, bucketPct * 0.28));

  return (
    <div className="relative h-14 w-full">
      {points.map((p) => {
        const bucketLeft = ((p.min - axisStart) / span) * 100;
        const left = bucketLeft + (bucketPct - barPct) / 2;
        const aH = (p.actual / maxH) * 100;
        const sH = (p.scheduled / maxH) * 100;
        return (
          <div
            key={p.min}
            className="absolute bottom-0 flex flex-col justify-end gap-px"
            style={{ left: `${left}%`, width: `${barPct}%`, height: "100%" }}
          >
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
    <div className="flex justify-between pt-1 text-[10px] text-muted-foreground">
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
        return (
          <div
            key={`${seg.kind}-${seg.startMin}-${i}`}
            className={cn(
              "absolute flex items-center justify-center overflow-hidden rounded-sm text-[10px] font-medium text-white",
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
}: {
  people: CoveragePersonDay[];
  points: OccupancyPoint[];
  axisStart: number;
  axisEnd: number;
}) {
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
        min: hover.minute,
      })
    : null;
  const shownCount = occ ? (occ.actual > 0 ? occ.actual : occ.scheduled) : 0;

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
        </p>
        {active.length ? (
          <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto">
            {active.map((p) => (
              <li
                key={`${p.employee}-${p.kind}`}
                className="flex items-start justify-between gap-3"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span
                    className="mt-0.5 inline-block size-2.5 shrink-0 rounded-sm"
                    style={{
                      backgroundColor: barColor(p.labor_bucket, p.kind),
                      opacity: p.kind === "scheduled" ? 0.7 : 1,
                    }}
                  />
                  <span className="truncate">{p.employee}</span>
                </span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {formatClockMin(p.startMin)}–{formatClockMin(p.endMin)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
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
            "shrink-0 pb-5 text-[10px] leading-none text-muted-foreground",
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
            return (
              <div key={p.employee} className="flex items-center gap-2">
                <div className={cn(GUTTER, "shrink-0 truncate")}>
                  <span className="block truncate text-xs font-medium" title={p.employee}>
                    {p.employee}
                  </span>
                  <span className="text-[10px] text-muted-foreground">
                    {totalHrs.toFixed(1)}h
                  </span>
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
        ) : (
          <p className="text-sm text-muted-foreground">No shifts for this day.</p>
        )}
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
  laborTypes,
  selectedDay,
  todayIso = chicagoTodayIso(),
}: {
  win: DateWindow;
  actuals: ActualShiftInput[];
  scheduled: ScheduledShiftInput[];
  laborTypes: string[] | null;
  selectedDay?: string;
  basePath?: string;
  extraParams?: Record<string, string>;
  todayIso?: string;
}) {
  const strip = useMemo(
    () => coverageStripDates(win),
    [win.start, win.end],
  );
  const initial = useMemo(
    () => resolveCoverageDay(selectedDay, strip, todayIso),
    [selectedDay, strip, todayIso],
  );
  const [day, setDay] = useState<string | null>(initial);
  const dayKey = `${initial ?? ""}|${strip.join(",")}`;
  const [prevKey, setPrevKey] = useState(dayKey);
  if (dayKey !== prevKey) {
    setPrevKey(dayKey);
    setDay(initial);
  }

  const chips = useMemo(
    () =>
      strip.map((iso) =>
        dayChipSummary(
          iso,
          buildPersonDaysForDate(iso, actuals, scheduled, laborTypes),
        ),
      ),
    [strip, actuals, scheduled, laborTypes],
  );

  const activeDay = day ?? initial;

  const people = useMemo(
    () =>
      activeDay
        ? buildPersonDaysForDate(activeDay, actuals, scheduled, laborTypes)
        : [],
    [activeDay, actuals, scheduled, laborTypes],
  );

  const bounds = useMemo(() => axisBounds(people), [people]);
  const points = useMemo(
    () => occupancySeries(people, bounds.startMin, bounds.endMin, 15),
    [people, bounds.startMin, bounds.endMin],
  );
  const narrative = useMemo(() => coverageNarrative(points), [points]);

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
          headcount + who is on (start–end). Solid = clocked; slate hatch = scheduled.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <DayStrip
          chips={chips}
          selected={activeDay}
          todayIso={todayIso}
          onSelect={(next) => {
            setDay(next);
            syncDayInUrl(next);
          }}
        />

        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-medium text-foreground">
            Coverage — {label.weekday} {label.monthDay}
          </h3>
          <p className="text-sm text-muted-foreground">{narrative}</p>
        </div>

        <CoverageTimeline
          people={people}
          points={points}
          axisStart={bounds.startMin}
          axisEnd={bounds.endMin}
        />

        <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
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
        </div>
      </CardContent>
    </Card>
  );
}
