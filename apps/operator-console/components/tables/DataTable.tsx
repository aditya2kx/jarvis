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
import {
  ArrowUpIcon,
  ArrowDownIcon,
  ChevronsUpDownIcon,
  ChevronDownIcon,
  CheckIcon,
} from "lucide-react";
import { Popover } from "@base-ui/react/popover";
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
import { filterTextOrMulti } from "@/lib/tables/column-filter";

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
    /** When DataTable `enableColumnFilters`, show a filter under the header. */
    filterable?: boolean;
    /** text (default) or multi-select checkboxes for categorical columns. */
    filterVariant?: "text" | "multi";
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
  return filterTextOrMulti(row.getValue(columnId), filterValue);
}

/** Compact trigger + floating checklist (Linear / Airtable faceted filter). */
function MultiSelectFilter({
  columnId,
  label,
  options,
  value,
  onChange,
}: {
  columnId: string;
  label: string;
  options: string[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selected = new Set(value);
  const active = selected.size > 0;
  const displayLabel = (() => {
    if (!active) return "All";
    if (selected.size === 1) {
      const only = [...selected][0];
      return only === "" ? "(blank)" : only;
    }
    return `${selected.size} selected`;
  })();
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((opt) =>
      (opt || "(blank)").toLowerCase().includes(q),
    );
  }, [options, query]);
  const showSearch = options.length > 6;

  function toggle(opt: string) {
    const next = new Set(selected);
    if (next.has(opt)) next.delete(opt);
    else next.add(opt);
    onChange([...next]);
  }

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <Popover.Trigger
        nativeButton
        type="button"
        onClick={(e) => e.stopPropagation()}
        aria-label={`Filter ${label}`}
        className={cn(
          "flex h-7 w-full min-w-0 items-center justify-between gap-1 rounded-md border px-2 text-left text-xs font-normal outline-none transition-colors",
          "focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40",
          active
            ? "border-primary/50 bg-primary/10 text-foreground"
            : "border-border bg-background text-muted-foreground hover:bg-muted/60 hover:text-foreground",
        )}
      >
        <span className="truncate">{displayLabel}</span>
        <ChevronDownIcon className="size-3.5 shrink-0 opacity-60" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="start" sideOffset={4} className="z-50">
          <Popover.Popup
            className={cn(
              "flex w-72 max-w-[min(18rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-lg border border-border bg-popover text-popover-foreground shadow-lg outline-none",
              "origin-[var(--transform-origin)] transition-[transform,scale,opacity] data-ending-style:scale-95 data-ending-style:opacity-0 data-starting-style:scale-95 data-starting-style:opacity-0",
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2 border-b border-border px-2.5 py-2">
              <span className="text-xs font-medium text-muted-foreground">{label}</span>
              <div className="flex items-center gap-2 text-[11px]">
                <button
                  type="button"
                  className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                  onClick={() => onChange([...options])}
                >
                  Select all
                </button>
                <button
                  type="button"
                  className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-40"
                  disabled={!active}
                  onClick={() => onChange([])}
                >
                  Clear
                </button>
              </div>
            </div>
            {showSearch ? (
              <div className="border-b border-border p-2">
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={`Search ${label.toLowerCase()}…`}
                  className="h-8 px-2 text-xs"
                  aria-label={`Search ${label}`}
                  autoFocus
                />
              </div>
            ) : null}
            <ul className="max-h-64 overflow-y-auto p-1" role="listbox" aria-multiselectable>
              {filtered.length === 0 ? (
                <li className="px-2 py-3 text-center text-xs text-muted-foreground">No matches</li>
              ) : (
                filtered.map((opt) => {
                  const checked = selected.has(opt);
                  const text = opt || "(blank)";
                  return (
                    <li key={`${columnId}-${opt || "(blank)"}`}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={checked}
                        className={cn(
                          "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted",
                          checked && "bg-muted/70",
                        )}
                        onClick={() => toggle(opt)}
                      >
                        <span
                          className={cn(
                            "mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-sm border",
                            checked
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-border",
                          )}
                          aria-hidden
                        >
                          {checked ? <CheckIcon className="size-2.5" strokeWidth={3} /> : null}
                        </span>
                        <span className="min-w-0 flex-1 whitespace-normal break-words leading-snug">
                          {text}
                        </span>
                      </button>
                    </li>
                  );
                })
              )}
            </ul>
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
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
  const filterActive =
    enableColumnFilters &&
    columnFilters.some((f) => {
      if (Array.isArray(f.value)) return f.value.length > 0;
      return String(f.value ?? "").trim().length > 0;
    });

  useEffect(() => {
    onFilteredRowsChange?.(filteredRows);
  }, [filteredRows, onFilteredRowsChange]);

  const filteredSpendEarned = useMemo(() => {
    if (!enableColumnFilters) return null;
    let spend = 0;
    let earned = 0;
    for (const r of filteredRows as Record<string, unknown>[]) {
      // S1: business-only strip — skip excluded / legacy internal even if rows visible.
      if (
        r.excluded === true ||
        r.excluded_label === "yes" ||
        r.is_internal === true ||
        r.internal_label === "yes"
      ) {
        continue;
      }
      const s = r.spend;
      const e = r.earned;
      if (typeof s === "number" && !Number.isNaN(s)) spend += s;
      if (typeof e === "number" && !Number.isNaN(e)) earned += e;
    }
    return { spend, earned };
  }, [enableColumnFilters, filteredRows]);

  const multiOptionsByCol = useMemo(() => {
    const map = new Map<string, string[]>();
    if (!enableColumnFilters) return map;
    const rows = data as Record<string, unknown>[];
    for (const col of columns) {
      const id =
        (col as { accessorKey?: string; id?: string }).accessorKey ||
        (col as { id?: string }).id;
      if (!id) continue;
      const meta = col.meta as { filterVariant?: string } | undefined;
      if (meta?.filterVariant !== "multi") continue;

      // Faceted options: values present after applying every OTHER column filter.
      const vals = new Set<string>();
      for (const row of rows) {
        let ok = true;
        for (const f of columnFilters) {
          if (f.id === id) continue;
          if (!filterTextOrMulti(row[f.id], f.value)) {
            ok = false;
            break;
          }
        }
        if (!ok) continue;
        const v = row[id];
        vals.add(v == null || v === "" ? "" : String(v));
      }
      // Keep currently selected values visible even if they temporarily drop out.
      const selected = columnFilters.find((f) => f.id === id)?.value;
      if (Array.isArray(selected)) {
        for (const s of selected) vals.add(String(s));
      }
      map.set(id, [...vals].sort((a, b) => a.localeCompare(b)));
    }
    return map;
  }, [columns, data, enableColumnFilters, columnFilters]);

  // Drop multi-select choices that no longer appear in the faceted option set
  // (e.g. subcategory after narrowing Category).
  useEffect(() => {
    if (!enableColumnFilters || multiOptionsByCol.size === 0) return;
    let changed = false;
    const next: ColumnFiltersState = [];
    for (const f of columnFilters) {
      const opts = multiOptionsByCol.get(f.id);
      if (!opts || !Array.isArray(f.value)) {
        next.push(f);
        continue;
      }
      const allow = new Set(opts);
      const pruned = (f.value as string[]).filter((v) => allow.has(String(v)));
      if (pruned.length !== (f.value as string[]).length) {
        changed = true;
        if (pruned.length) next.push({ ...f, value: pruned });
        continue;
      }
      next.push(f);
    }
    if (changed) setColumnFilters(next);
  }, [columnFilters, enableColumnFilters, multiOptionsByCol]);
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
                          meta?.filterVariant === "multi" ? (
                            <MultiSelectFilter
                              columnId={header.column.id}
                              label={String(header.column.columnDef.header)}
                              options={multiOptionsByCol.get(header.column.id) || []}
                              value={
                                Array.isArray(header.column.getFilterValue())
                                  ? (header.column.getFilterValue() as string[])
                                  : []
                              }
                              onChange={(next) =>
                                header.column.setFilterValue(next.length ? next : undefined)
                              }
                            />
                          ) : (
                            <Input
                              value={String(header.column.getFilterValue() ?? "")}
                              onChange={(e) => header.column.setFilterValue(e.target.value)}
                              onClick={(e) => e.stopPropagation()}
                              placeholder="Filter…"
                              className="h-7 w-full min-w-0 px-2 text-xs font-normal"
                              aria-label={`Filter ${String(header.column.columnDef.header)}`}
                            />
                          )
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
