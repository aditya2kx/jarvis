"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import {
  type ColumnDef,
  type ColumnPinningState,
  type RowData,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { formatDate, formatDollars, formatCents, formatNumber, formatPct } from "@/lib/format";
import { cn } from "@/lib/utils";

// Threshold coloring for numeric/pct/dollars columns (Figma: red/amber/green
// on p95, % late, Days-left, wage-diff). `warn`/`bad` are in the same unit as
// the raw cell value (e.g. a "pct" column's thresholds are fractions like
// 0.05, matching the value before the *100 display conversion). `useAbs`
// compares |value| — for columns like wage-diff where either direction of a
// large gap is the problem, not just one sign.
export interface Thresholds {
  warn: number;
  bad: number;
  direction: "higher-bad" | "lower-bad";
  useAbs?: boolean;
}

// Column `cell` render functions can't cross the Server->Client Component
// boundary (they're closures created in the page's server render, and RSC
// props must be serializable) — see docs/operator-console/PLAN.md decisions
// log. So every page passes a serializable `meta.format` tag instead of a
// `cell` fn, and DataTable — already a client component — owns rendering.
export type ColumnFormat =
  | { kind: "date" }
  | { kind: "dollars"; thresholds?: Thresholds }
  | { kind: "cents" }
  | { kind: "pct"; digits?: number; thresholds?: Thresholds }
  | { kind: "number"; digits?: number; thresholds?: Thresholds }
  | { kind: "status" }
  | { kind: "source" };

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    format?: ColumnFormat;
  }
}

function statusVariant(value: string | null | undefined): "default" | "destructive" | "secondary" {
  if (value === "success") return "default";
  if (value) return "destructive";
  return "secondary";
}

function thresholdClass(value: number | null | undefined, t: Thresholds): string | undefined {
  if (value == null || Number.isNaN(value)) return undefined;
  const v = t.useAbs ? Math.abs(value) : value;
  const bad = t.direction === "higher-bad" ? v >= t.bad : v <= t.bad;
  if (bad) return "text-red-500 font-medium";
  const warn = t.direction === "higher-bad" ? v >= t.warn : v <= t.warn;
  if (warn) return "text-amber-500 font-medium";
  return "text-emerald-500 font-medium";
}

function renderFormatted(format: ColumnFormat, value: unknown): ReactNode {
  switch (format.kind) {
    case "date":
      return formatDate(value as Parameters<typeof formatDate>[0]);
    case "dollars": {
      const v = value as number | null | undefined;
      const cls = format.thresholds ? thresholdClass(v, format.thresholds) : undefined;
      return <span className={cls}>{formatDollars(v)}</span>;
    }
    case "cents":
      return formatCents(value as number | null | undefined);
    case "pct": {
      const v = value as number | null | undefined;
      const cls = format.thresholds ? thresholdClass(v, format.thresholds) : undefined;
      return <span className={cls}>{formatPct(v, format.digits)}</span>;
    }
    case "number": {
      const v = value as number | null | undefined;
      const cls = format.thresholds ? thresholdClass(v, format.thresholds) : undefined;
      return <span className={cls}>{formatNumber(v, format.digits)}</span>;
    }
    case "status":
      return <Badge variant={statusVariant(value as string | null | undefined)}>{(value as string) ?? "unknown"}</Badge>;
    case "source": {
      const v = value as "Estimated" | "Actuals" | null | undefined;
      if (!v) return null; // no second date registered yet (vw_order_reco_combined §Source 2)
      return <Badge variant={v === "Actuals" ? "default" : "secondary"}>{v}</Badge>;
    }
  }
}

// Thin TanStack wrapper. `pinLeft` mirrors Grafana panel 83's
// `options.frozenColumns.left` — used by the M3 dual-date reco table to keep
// Item/Current Qty/Avg per day visible while scrolling the date groups.
export function DataTable<TData>({
  columns,
  data,
  pinLeft = [],
}: {
  columns: ColumnDef<TData>[];
  data: TData[];
  pinLeft?: string[];
}) {
  const columnPinning: ColumnPinningState = { left: pinLeft, right: [] };

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    state: { columnPinning },
    onColumnPinningChange: () => {},
  });

  // Multiple pinned columns each need a *cumulative* left offset — TanStack's
  // own getStart("left") assumes the 150px default column size, but these
  // columns are content-driven (no explicit `size`), so offsets are measured
  // from the actually-rendered header cells instead of computed from state.
  const containerRef = useRef<HTMLDivElement>(null);
  const [pinOffsets, setPinOffsets] = useState<Record<string, number>>({});
  const [atEnd, setAtEnd] = useState(true);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const heads = el.querySelectorAll<HTMLElement>('thead th[data-pinned="left"]');
      let acc = 0;
      const next: Record<string, number> = {};
      heads.forEach((h) => {
        const colId = h.dataset.colId!;
        next[colId] = acc;
        acc += h.offsetWidth;
      });
      setPinOffsets(next);
      setAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 1);
    };
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      ro.disconnect();
    };
  }, [columns, data]);

  const lastPinnedId = pinLeft[pinLeft.length - 1];

  return (
    <div className="relative overflow-hidden rounded-md border border-border">
      <Table containerRef={containerRef}>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((header) => {
                const pinned = header.column.getIsPinned();
                return (
                  <TableHead
                    key={header.id}
                    data-pinned={pinned || undefined}
                    data-col-id={header.column.id}
                    style={pinned ? { left: pinOffsets[header.column.id] ?? 0 } : undefined}
                    className={cn(
                      pinned && "sticky z-10 bg-background",
                      pinned && header.column.id === lastPinnedId && "border-r border-border",
                    )}
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => {
                  const format = cell.column.columnDef.meta?.format;
                  const pinned = cell.column.getIsPinned();
                  return (
                    <TableCell
                      key={cell.id}
                      style={pinned ? { left: pinOffsets[cell.column.id] ?? 0 } : undefined}
                      className={cn(
                        pinned && "sticky z-10 bg-background",
                        pinned && cell.column.id === lastPinnedId && "border-r border-border",
                      )}
                    >
                      {format
                        ? renderFormatted(format, cell.getValue())
                        : flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={columns.length} className="text-center text-muted-foreground">
                No rows.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      {!atEnd ? (
        <div className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-gradient-to-l from-background" />
      ) : null}
    </div>
  );
}
