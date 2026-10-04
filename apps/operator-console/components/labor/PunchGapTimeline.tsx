import { LABOR_CHART_COLORS } from "@/lib/charts/palette";
import { formatClock, openClockIn, pct, toMinutes, type PunchGap } from "@/lib/labor/punch-gaps";

const CLOCKED = LABOR_CHART_COLORS.parttimeActual;
const SCHED = LABOR_CHART_COLORS.parttimeScheduled;
const PROPOSED = "#f59e0b"; // amber-500 — matches the Missing clock-out badge
const PROPOSED_HATCH = `repeating-linear-gradient(-45deg, ${PROPOSED}55, ${PROPOSED}55 2px, transparent 2px, transparent 4px)`;

type Bounds = { start: number; end: number };

function span(a: string | null, b: string | null, bounds: Bounds) {
  const s = a ? toMinutes(a) : null;
  const e = b ? toMinutes(b) : null;
  if (s == null || e == null || e <= s) return null;
  const left = pct(s, bounds);
  return { left, width: Math.max(pct(e, bounds) - left, 0.8) };
}

function ticks(bounds: Bounds): number[] {
  const out: number[] = [];
  const step = bounds.end - bounds.start > 12 * 60 ? 180 : 120;
  for (let m = Math.ceil(bounds.start / step) * step; m <= bounds.end; m += step) out.push(m);
  return out;
}

function hourLabel(min: number): string {
  const h = Math.floor(min / 60);
  return `${h % 12 || 12}${h < 12 ? "a" : "p"}`;
}

/** Hour ticks for the timeline column header (shared axis across rows). */
export function PunchGapTimelineAxis({ bounds }: { bounds: Bounds }) {
  return (
    <div className="relative h-4 w-full text-[10px] font-normal text-muted-foreground">
      {ticks(bounds).map((m) => (
        <span
          key={m}
          className="absolute -translate-x-1/2 tabular-nums"
          style={{ left: `${pct(m, bounds)}%` }}
        >
          {hourLabel(m)}
        </span>
      ))}
    </div>
  );
}

/**
 * One employee-day: scheduled shift(s) as slate dashed outlines, completed
 * punches solid, and the open entry as a solid clock-in tick plus an amber
 * hatched span to the suggested clock-out (for a day with no punch, the whole
 * suggested entry) — so "what we'd be paying for" is the hatched part,
 * visibly separate from time actually punched.
 */
export function PunchGapTimeline({ gap, bounds }: { gap: PunchGap; bounds: Bounds }) {
  const clockIn =
    gap.kind === "no_entry" ? (gap.decision?.inTime ?? gap.suggestedIn) : openClockIn(gap);
  const proposedOut = gap.decision?.outTime ?? gap.suggestedOut;
  const proposed =
    gap.decision?.action === "reject" ? null : span(clockIn, proposedOut, bounds);
  const label = `${gap.employee} ${gap.date}: scheduled ${
    gap.scheduled.map(([a, b]) => `${formatClock(a)}–${formatClock(b)}`).join(", ") || "none"
  }; punched ${
    gap.entries.map((e) => `${formatClock(e.in)}–${e.out ? formatClock(e.out) : "open"}`).join(", ") ||
    "none"
  }`;

  return (
    <div
      role="img"
      aria-label={label}
      className="relative h-7 w-full overflow-hidden rounded-md bg-muted/40"
    >
      {ticks(bounds).map((m) => (
        <span
          key={`t-${m}`}
          className="absolute inset-y-0 w-px bg-border/60"
          style={{ left: `${pct(m, bounds)}%` }}
        />
      ))}
      {gap.scheduled.map(([a, b], i) => {
        const s = span(a, b, bounds);
        return s ? (
          <span
            key={`s-${i}`}
            className="absolute inset-y-0.5 rounded-sm border border-dashed"
            style={{ left: `${s.left}%`, width: `${s.width}%`, borderColor: SCHED }}
          />
        ) : null;
      })}
      {proposed ? (
        <span
          className="absolute top-[9px] h-2.5 rounded-sm border border-dashed"
          style={{
            left: `${proposed.left}%`,
            width: `${proposed.width}%`,
            borderColor: PROPOSED,
            background: PROPOSED_HATCH,
          }}
        />
      ) : null}
      {gap.entries.map((e, i) => {
        const s = span(e.in, e.out, bounds);
        if (s) {
          return (
            <span
              key={`e-${i}`}
              className="absolute top-[9px] h-2.5 rounded-sm"
              style={{ left: `${s.left}%`, width: `${s.width}%`, backgroundColor: CLOCKED }}
            />
          );
        }
        const t = e.in ?? e.out;
        const m = t ? toMinutes(t) : null;
        return m != null ? (
          <span
            key={`e-${i}`}
            className="absolute top-1.5 h-4 w-1 -translate-x-1/2 rounded-full"
            style={{ left: `${pct(m, bounds)}%`, backgroundColor: CLOCKED }}
          />
        ) : null;
      })}
    </div>
  );
}
