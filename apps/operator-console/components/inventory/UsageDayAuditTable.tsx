"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { UsageDayAuditRow } from "@/lib/bq/queries";
import {
  cellKey,
  formatQty,
  pivotUsageDayAudit,
  statusTag,
} from "@/lib/inventory/usageDayAudit";
import { UsageDayOverrideDrawer } from "@/components/inventory/UsageDayOverrideDrawer";

/** ~10 body rows visible; header sticky inside the scrollport. */
const TABLE_MAX_H = "max-h-[min(36rem,70vh)]";

function chipVariant(
  row: UsageDayAuditRow,
): "default" | "secondary" | "destructive" | "outline" {
  if (row.override_mode === "force_include") return "default";
  if (row.override_mode === "force_exclude") return "destructive";
  if (row.status === "included") return "secondary";
  return "outline";
}

function BaseCell({ row }: { row: UsageDayAuditRow | undefined }) {
  if (!row) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  return (
    <div className="flex min-h-10 min-w-[6.5rem] flex-col items-start gap-0.5 px-1.5 py-1">
      <span className="text-sm font-medium tabular-nums">{formatQty(row.qty)}</span>
      <Badge variant={chipVariant(row)} className="max-w-full truncate text-[10px] leading-tight">
        {statusTag(row)}
      </Badge>
    </div>
  );
}

export function UsageDayAuditTable({
  rows,
  writable,
}: {
  rows: UsageDayAuditRow[];
  writable: boolean;
}) {
  const matrix = useMemo(() => pivotUsageDayAudit(rows), [rows]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  if (!matrix.dates.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No closing readings in the last 30 days.
      </p>
    );
  }

  return (
    <>
      <div
        className={`overflow-auto rounded-lg border border-border ${TABLE_MAX_H}`}
        data-testid="usage-day-audit"
      >
        <table className="w-max min-w-full border-collapse text-left text-sm">
          <thead className="sticky top-0 z-30 border-b border-border bg-muted/95 backdrop-blur supports-[backdrop-filter]:bg-muted/80">
            <tr>
              <th className="sticky left-0 z-40 bg-muted/95 px-3 py-2 font-medium shadow-[2px_0_4px_-2px_rgba(0,0,0,0.15)] backdrop-blur supports-[backdrop-filter]:bg-muted/80">
                Date
              </th>
              {matrix.bases.map((base) => (
                <th key={base} className="px-2 py-2 font-medium whitespace-nowrap">
                  {base}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.dates.map((date) => (
              <tr
                key={date}
                className="group cursor-pointer border-b border-border/60 align-top hover:bg-muted/30"
                onClick={() => setSelectedDate(date)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedDate(date);
                  }
                }}
                tabIndex={0}
                title="Open day editor"
              >
                <td className="sticky left-0 z-10 bg-background px-3 py-2 font-medium whitespace-nowrap shadow-[2px_0_4px_-2px_rgba(0,0,0,0.12)] group-hover:bg-muted/30">
                  {date}
                </td>
                {matrix.bases.map((base) => (
                  <td key={base} className="px-1 py-1">
                    <BaseCell row={matrix.cells.get(cellKey(date, base))} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <UsageDayOverrideDrawer
        open={selectedDate != null}
        onOpenChange={(open) => {
          if (!open) setSelectedDate(null);
        }}
        date={selectedDate}
        rows={rows}
        writable={writable}
      />
    </>
  );
}
