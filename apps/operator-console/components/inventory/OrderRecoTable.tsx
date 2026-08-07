"use client";

import { useMemo, useState } from "react";
import { PencilIcon } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable, type Thresholds } from "@/components/tables/DataTable";
import { Badge } from "@/components/ui/badge";
import {
  EstimateTubsDrawer,
  type EstimateTubRow,
} from "@/components/inventory/EstimateTubsDrawer";
import {
  normalizeDeliveryDate,
  type OrderRecoPivotedRow,
} from "@/lib/inventory/orderRecoPivot";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

const DAYS_LEFT_THRESHOLDS: Thresholds = { warn: 7, bad: 4, direction: "lower-bad" };

/**
 * Dual-date reco table with Order Tubs click → batch estimate drawer (Issue #225).
 * Client-owned so cell renderers / click handlers stay on this side of the RSC boundary.
 */
export function OrderRecoTable({
  dates,
  estimatedDates,
  rows,
  maxTubs,
  writable,
}: {
  dates: string[];
  estimatedDates: string[];
  rows: OrderRecoPivotedRow[];
  maxTubs?: number;
  writable: boolean;
}) {
  const estimatedSet = useMemo(
    () => new Set(estimatedDates.map((d) => normalizeDeliveryDate(d))),
    [estimatedDates],
  );
  const [openDate, setOpenDate] = useState<string | null>(null);

  const drawerRows: EstimateTubRow[] = useMemo(() => {
    if (!openDate) return [];
    const slot = dates.indexOf(openDate) + 1;
    if (slot < 1) return [];
    return rows
      .filter((r) => r.Item !== "TOTAL")
      .map((r) => ({
        item: String(r.Item),
        orderTubs: Number(r[`Order Tubs ${slot}`] ?? 0),
        source: (r[`Source ${slot}`] as EstimateTubRow["source"]) ?? "Estimated",
      }));
  }, [openDate, dates, rows]);

  const columns = useMemo((): ColumnDef<OrderRecoPivotedRow>[] => {
    const cols: ColumnDef<OrderRecoPivotedRow>[] = [
      { accessorKey: "Item", header: "Item" },
      {
        accessorKey: "Current Qty",
        header: "Current Qty",
        meta: { format: { kind: "number", digits: 1 } },
      },
      {
        accessorKey: "Avg per day",
        header: "Avg/day",
        meta: { format: { kind: "number", digits: 2 } },
      },
    ];

    dates.forEach((raw, i) => {
      const slot = i + 1;
      const date = normalizeDeliveryDate(raw) || `slot ${slot}`;
      const isEstimated = estimatedSet.has(date);
      const canEdit = writable && isEstimated;

      cols.push(
        {
          accessorKey: `On Hand ${slot}`,
          header: `On hand (${date})`,
          meta: { format: { kind: "number", digits: 1 } },
        },
        {
          accessorKey: `Order Tubs ${slot}`,
          enableSorting: false,
          header: () => (
            <span className="inline-flex items-center gap-1">
              Order tubs
              {canEdit ? (
                <button
                  type="button"
                  className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`Edit estimates for ${date}`}
                  title={`Edit estimates · ${date}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setOpenDate(date);
                  }}
                >
                  <PencilIcon className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </span>
          ),
          cell: ({ getValue, row }) => {
            const rawVal = getValue();
            const n = rawVal == null || rawVal === "" ? null : Number(rawVal);
            const display = n == null || Number.isNaN(n) ? "—" : formatNumber(n, 0);
            const source = row.original[`Source ${slot}`] as string | null | undefined;
            if (!canEdit) {
              return <span className="tabular-nums">{display}</span>;
            }
            return (
              <button
                type="button"
                className={cn(
                  "group inline-flex min-h-10 min-w-[3.5rem] items-center gap-1 rounded-md px-1.5 -mx-1.5",
                  "text-left tabular-nums hover:bg-muted/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                )}
                onClick={() => setOpenDate(date)}
                aria-label={`Edit Order tubs for ${date}`}
                title={`Edit estimates · ${date}`}
              >
                <span>{display}</span>
                {source === "Manual" ? (
                  <Badge variant="outline" className="text-[10px] font-normal leading-tight">
                    Manual
                  </Badge>
                ) : (
                  <PencilIcon className="h-3 w-3 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
                )}
              </button>
            );
          },
        },
        {
          accessorKey: `Order Weight ${slot}`,
          header: "Order weight (lbs)",
          meta: { format: { kind: "number", digits: 0 } },
        },
        {
          accessorKey: `After Restock ${slot}`,
          header: "After restock",
          meta: { format: { kind: "number", digits: 1 } },
        },
        {
          accessorKey: `Days Left ${slot}`,
          header: "Days left",
          meta: { format: { kind: "number", digits: 1, thresholds: DAYS_LEFT_THRESHOLDS } },
        },
        {
          accessorKey: `Source ${slot}`,
          header: "Source",
          meta: { format: { kind: "source" } },
        },
      );
    });

    return cols;
  }, [dates, estimatedSet, writable]);

  return (
    <>
      <DataTable
        columns={columns}
        data={rows}
        pinLeft={["Item", "Current Qty", "Avg per day"]}
      />
      {writable ? (
        <EstimateTubsDrawer
          open={openDate != null}
          onOpenChange={(o) => {
            if (!o) setOpenDate(null);
          }}
          deliveryDate={openDate}
          rows={drawerRows}
          maxTubs={maxTubs}
        />
      ) : null}
    </>
  );
}
