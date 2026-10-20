"use client";

import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useActiveTooltipLabel } from "recharts";
import { cn } from "@/lib/utils";

export type ReadoutRow = { label: string; value: string; color?: string };
export type Readout = { title: string; rows: ReadoutRow[]; notes?: string[] };

/** Series the operator tapped off in the chip legend (Recharts `hide`). */
export function useHiddenSeries() {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (key: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return { hidden, toggle };
}

/**
 * Tap-to-toggle legend chips above the plot. Replaces Recharts' static legend so a busy
 * multi-series chart can be narrowed to one line or bar group on any screen size; the last
 * visible series cannot be turned off.
 */
export function SeriesToggles({
  items,
  hidden,
  onToggle,
}: {
  items: { key: string; label: string; color: string }[];
  hidden: ReadonlySet<string>;
  onToggle: (key: string) => void;
}) {
  if (items.length < 2) return null;
  const visible = items.length - items.filter((i) => hidden.has(i.key)).length;
  return (
    <div
      role="group"
      aria-label="Show or hide series"
      className="flex flex-wrap gap-1.5 max-md:-mx-1 max-md:flex-nowrap max-md:overflow-x-auto max-md:px-1 max-md:[scrollbar-width:none] max-md:[&>*]:shrink-0"
      data-testid="series-toggles"
    >
      {items.map((item) => {
        const on = !hidden.has(item.key);
        return (
          <button
            key={item.key}
            type="button"
            aria-pressed={on}
            disabled={on && visible === 1}
            onClick={() => onToggle(item.key)}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:h-8",
              on
                ? "border-border bg-background text-foreground hover:bg-muted"
                : "border-dashed border-border bg-transparent text-muted-foreground line-through decoration-muted-foreground/60",
              "disabled:cursor-default",
            )}
          >
            <span
              aria-hidden
              className={cn("size-2.5 shrink-0 rounded-full", !on && "opacity-40")}
              style={{ background: item.color }}
            />
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export function ReadoutBody({ readout, compact = false }: { readout: Readout; compact?: boolean }) {
  // One series per line: names like "Part-time (scheduled)" don't survive a two-column grid.
  return (
    <>
      <p className={cn("font-medium text-foreground", compact ? "mb-1.5" : "mb-1")}>{readout.title}</p>
      <ul className="flex flex-col gap-1">
        {readout.rows.map((row) => (
          <li key={row.label} className="flex min-w-0 items-center justify-between gap-3">
            <span className="flex min-w-0 items-center gap-1.5 text-muted-foreground">
              {row.color ? (
                <span aria-hidden className="inline-block size-2.5 shrink-0 rounded-sm" style={{ background: row.color }} />
              ) : null}
              <span className="truncate">{row.label}</span>
            </span>
            <span className="font-medium tabular-nums text-foreground">{row.value}</span>
          </li>
        ))}
      </ul>
      {readout.notes?.length ? (
        <ul className="mt-1.5 flex flex-col gap-0.5 border-t border-border pt-1.5 text-muted-foreground">
          {readout.notes.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

/** Desktop hover tooltip: the same readout in a floating card. */
export function FloatingReadout({ readout }: { readout: Readout | null }) {
  if (!readout || (!readout.rows.length && !readout.notes?.length)) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-md">
      <ReadoutBody readout={readout} compact />
    </div>
  );
}

/**
 * Phone scrub readout. Rendered inside the Recharts tree (so the active-point hooks work)
 * but portalled into a strip above the plot: the value sits where the thumb isn't, and it
 * stays after the finger lifts, like a stock chart. `build` maps the active x label to rows.
 */
export function ScrubReadout({
  target,
  build,
}: {
  target: HTMLElement | null;
  build: (label: string) => Readout | null;
}) {
  const label = useActiveTooltipLabel();
  if (!target) return null;
  const readout = label != null ? build(String(label)) : null;
  return createPortal(
    readout ? (
      <div data-testid="chart-readout" className="text-xs">
        <ReadoutBody readout={readout} />
      </div>
    ) : (
      <p className="text-xs text-muted-foreground">Tap or drag across the chart to see values.</p>
    ),
    target,
  );
}

/** Strip that receives the ScrubReadout portal; reserved height avoids a jump on first tap. */
export function ReadoutSlot({ onMount, children }: { onMount: (el: HTMLDivElement | null) => void; children?: ReactNode }) {
  return (
    <div
      ref={onMount}
      aria-live="polite"
      className="min-h-10 rounded-lg bg-muted/40 px-3 py-2 md:hidden"
    >
      {children}
    </div>
  );
}

export function axisLabel(raw: unknown): string {
  return String(raw ?? "").replace(/,\n/g, ", ").replace(/\n/g, " · ");
}
