"use client";

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  type ColumnDef,
  type ColumnFiltersState,
  type ColumnPinningState,
  type RowData,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowUpIcon, ArrowDownIcon, ChevronsUpDownIcon } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { formatDate, formatDollars, formatCents, formatNumber, formatPct } from "@/lib/format";
import { formatBucket, type Grain } from "@/lib/filters/range";
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
  // Grain-aware date bucket (Issue #132 follow-up) — a week/month bucket
  // isn't a plain calendar day, so it needs `formatBucket`'s "Wk of …"/"Jan
  // 2026" shapes rather than "date"'s day-of-month rendering.
  | { kind: "bucket"; grain: Grain }
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
    /** When DataTable `enableColumnFilters`, show a text filter under the header. */
    filterable?: boolean;
    /** Allow multi-line cell text (overrides table `whitespace-nowrap`). */
    wrap?: boolean;
    /** Cap column width in px (use with `wrap` for long ACH/description strings). */
    maxWidth?: number;
    /** Preferred column width in px (`table-layout: fixed`). */
    width?: number;
  }
}

function columnLayoutStyle(meta: { maxWidth?: number; width?: number } | undefined): CSSProperties {
  if (!meta) return {};
  const style: CSSProperties = {};
  if (meta.width != null) style.width = meta.width;
  if (meta.maxWidth != null) {
    style.maxWidth = meta.maxWidth;
    style.minWidth = Math.min(meta.width ?? 96, meta.maxWidth);
  }
  return style;
}

function statusVariant(value: string | null | undefined): "default" | "destructive" | "secondary" {
  if (value === "success" || value === "Fine" || value === "Covered") return "default";
  if (value === "Risky") return "destructive";
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
    case "bucket":
      return formatBucket(value as Parameters<typeof formatBucket>[0], format.grain);
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
      if (value == null || value === "") return null; // no slot 2 yet (Status 2)
      return <Badge variant={statusVariant(value as string)}>{value as string}</Badge>;
    case "source": {
      const v = value as "Estimated" | "Actuals" | null | undefined;
      if (!v) return null; // no second date registered yet (vw_order_reco_combined §Source 2)
      return <Badge variant={v === "Actuals" ? "default" : "secondary"}>{v}</Badge>;
    }
  }
}

function filterIncludesString(
  row: { getValue: (columnId: string) => unknown },
  columnId: string,
  filterValue: unknown,
): boolean {
  const needle = String(filterValue ?? "")
    .trim()
    .toLowerCase();
  if (!needle) return true;
  const rowValue = row.getValue(columnId);
  if (rowValue == null || rowValue === "") return false;
  return String(rowValue).toLowerCase().includes(needle);
}

// Thin TanStack wrapper. `pinLeft` mirrors Grafana panel 83's
// `options.frozenColumns.left` — used by the M3 dual-date reco table to keep
// Item/Current Qty/Avg per day visible while scrolling the date groups.
export function DataTable<TData>({
  columns,
  data,
  pinLeft = [],
  initialSorting = [],
  rowHighlight,
  enableColumnFilters = false,
  onFilteredRowsChange,
}: {
  columns: ColumnDef<TData>[];
  data: TData[];
  pinLeft?: string[];
  initialSorting?: SortingState;
  /** Serializable row tint (RSC-safe). When any rule matches `row[accessorKey] === equals`, apply that className (OR). */
  rowHighlight?:
    | { accessorKey: string; equals: string; className: string }
    | { accessorKey: string; equals: string; className: string }[];
  /** Per-column text filters under headers (Accounting transactions, etc.). */
  enableColumnFilters?: boolean;
  /** Fired when the filtered (visible) row set changes — used by Accounting KPIs. */
  onFilteredRowsChange?: (rows: TData[]) => void;
}) {
  const columnPinning: ColumnPinningState = { left: pinLeft, right: [] };
  // Client-side sort across every column — Grafana's table panels let an
  // operator click any header to sort; this is the console-side equivalent.
  // Optional `enableColumnFilters` adds per-column text filters for dense
  // ledgers (Accounting) without replacing page-level FilterSelect controls.
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: enableColumnFilters ? getFilteredRowModel() : undefined,
    state: { columnPinning, sorting, columnFilters },
    onColumnPinningChange: () => {},
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    enableColumnFilters,
    defaultColumn: {
      filterFn: filterIncludesString,
      enableColumnFilter: enableColumnFilters,
    },
  });

  const filteredRows = useMemo(
    () => table.getFilteredRowModel().rows.map((r) => r.original),
    // columnFilters + data drive the filtered model; table identity is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional deps
    [table, columnFilters, data, enableColumnFilters],
  );
  const filteredCount = filteredRows.length;
  const filterActive = enableColumnFilters && columnFilters.some((f) => String(f.value ?? "").trim());

  useEffect(() => {
    onFilteredRowsChange?.(filteredRows);
  }, [filteredRows, onFilteredRowsChange]);

  const filteredSpendEarned = useMemo(() => {
    if (!enableColumnFilters) return null;
    let spend = 0;
    let earned = 0;
    for (const r of filteredRows as Record<string, unknown>[]) {
      // Align caption with Accounting KPIs: skip internal transfers.
      if (r.is_internal === true || r.internal_label === "yes") continue;
      const s = r.spend;
      const e = r.earned;
      if (typeof s === "number" && !Number.isNaN(s)) spend += s;
      if (typeof e === "number" && !Number.isNaN(e)) earned += e;
    }
    return { spend, earned };
  }, [enableColumnFilters, filteredRows]);

  function rowClassName(row: TData): string | undefined {
    if (!rowHighlight) return undefined;
    const rules = Array.isArray(rowHighlight) ? rowHighlight : [rowHighlight];
    for (const rule of rules) {
      const v = (row as Record<string, unknown>)[rule.accessorKey];
      if (v === rule.equals) return rule.className;
    }
    return undefined;
  }

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
  }, [columns, data, columnFilters]);

  const lastPinnedId = pinLeft[pinLeft.length - 1];
  const useFixedLayout = columns.some((c) => {
    const meta = c.meta as { wrap?: boolean; maxWidth?: number; width?: number } | undefined;
    return Boolean(meta?.wrap || meta?.maxWidth != null || meta?.width != null);
  });

  return (
    <div className="flex flex-col gap-2">
      {enableColumnFilters ? (
        <p className="text-xs text-muted-foreground">
          Showing {filteredCount.toLocaleString()} of {data.length.toLocaleString()} transactions
          {filterActive ? " (filtered)" : ""}
          {filteredSpendEarned
            ? ` · spend ${formatDollars(filteredSpendEarned.spend)} · earned ${formatDollars(filteredSpendEarned.earned)}`
            : null}
          {filterActive ? (
            <button
              type="button"
              className="ml-2 underline hover:text-foreground"
              onClick={() => setColumnFilters([])}
            >
              Clear filters
            </button>
          ) : null}
        </p>
      ) : null}
      <div className="relative overflow-hidden rounded-md border border-border">
        <Table containerRef={containerRef} className={cn(useFixedLayout && "table-fixed")}>
          <TableHeader>
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id}>
                {hg.headers.map((header) => {
                  const pinned = header.column.getIsPinned();
                  const sortable = header.column.getCanSort();
                  const sortDir = header.column.getIsSorted();
                  const meta = header.column.columnDef.meta;
                  const showFilter =
                    enableColumnFilters &&
                    header.column.getCanFilter() &&
                    meta?.filterable !== false;
                  return (
                    <TableHead
                      key={header.id}
                      data-pinned={pinned || undefined}
                      data-col-id={header.column.id}
                      style={{
                        ...(pinned ? { left: pinOffsets[header.column.id] ?? 0 } : {}),
                        ...columnLayoutStyle(meta),
                      }}
                      className={cn(
                        "align-top",
                        meta?.wrap && "whitespace-normal",
                        pinned && "sticky z-10 bg-background",
                        pinned && header.column.id === lastPinnedId && "border-r border-border",
                      )}
                    >
                      <div className="flex flex-col gap-1.5">
                        {sortable ? (
                          <button
                            type="button"
                            onClick={header.column.getToggleSortingHandler()}
                            className="flex items-center gap-1 hover:text-foreground"
                            aria-label={`Sort by ${String(header.column.columnDef.header)}`}
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {sortDir === "asc" ? (
                              <ArrowUpIcon className="size-3" />
                            ) : sortDir === "desc" ? (
                              <ArrowDownIcon className="size-3" />
                            ) : (
                              <ChevronsUpDownIcon className="size-3 text-muted-foreground/50" />
                            )}
                          </button>
                        ) : (
                          flexRender(header.column.columnDef.header, header.getContext())
                        )}
                        {showFilter ? (
                          <Input
                            value={String(header.column.getFilterValue() ?? "")}
                            onChange={(e) => header.column.setFilterValue(e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                            placeholder="Filter…"
                            className="h-7 w-full min-w-0 px-2 text-xs font-normal"
                            aria-label={`Filter ${String(header.column.columnDef.header)}`}
                          />
                        ) : null}
                      </div>
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} className={rowClassName(row.original)}>
                  {row.getVisibleCells().map((cell) => {
                    const format = cell.column.columnDef.meta?.format;
                    const meta = cell.column.columnDef.meta;
                    const pinned = cell.column.getIsPinned();
                    return (
                      <TableCell
                        key={cell.id}
                        style={{
                          ...(pinned ? { left: pinOffsets[cell.column.id] ?? 0 } : {}),
                          ...columnLayoutStyle(meta),
                        }}
                        className={cn(
                          meta?.wrap
                            ? "whitespace-normal break-words align-top"
                            : undefined,
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
    </div>
  );
}
