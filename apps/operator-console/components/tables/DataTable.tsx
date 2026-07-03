"use client";

import type { ReactNode } from "react";
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

// Column `cell` render functions can't cross the Server->Client Component
// boundary (they're closures created in the page's server render, and RSC
// props must be serializable) — see docs/operator-console/PLAN.md decisions
// log. So every page passes a serializable `meta.format` tag instead of a
// `cell` fn, and DataTable — already a client component — owns rendering.
export type ColumnFormat =
  | { kind: "date" }
  | { kind: "dollars" }
  | { kind: "cents" }
  | { kind: "pct"; digits?: number }
  | { kind: "number"; digits?: number }
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

function renderFormatted(format: ColumnFormat, value: unknown): ReactNode {
  switch (format.kind) {
    case "date":
      return formatDate(value as Parameters<typeof formatDate>[0]);
    case "dollars":
      return formatDollars(value as number | null | undefined);
    case "cents":
      return formatCents(value as number | null | undefined);
    case "pct":
      return formatPct(value as number | null | undefined, format.digits);
    case "number":
      return formatNumber(value as number | null | undefined, format.digits);
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

  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((header) => (
                <TableHead
                  key={header.id}
                  className={cn(
                    header.column.getIsPinned() && "sticky left-0 z-10 bg-background",
                  )}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getVisibleCells().map((cell) => {
                  const format = cell.column.columnDef.meta?.format;
                  return (
                    <TableCell
                      key={cell.id}
                      className={cn(
                        cell.column.getIsPinned() && "sticky left-0 z-10 bg-background",
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
    </div>
  );
}
