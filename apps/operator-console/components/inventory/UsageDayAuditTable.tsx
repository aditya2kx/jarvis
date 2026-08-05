"use client";

import { useCallback, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import type { UsageDayAuditRow } from "@/lib/bq/queries";
import {
  cellKey,
  formatQty,
  matrixChipVariant,
  matrixStatusTag,
  pivotUsageDayAudit,
} from "@/lib/inventory/usageDayAudit";
import {
  UsageDayOverrideDrawer,
  type UsageDayOverridesApplied,
} from "@/components/inventory/UsageDayOverrideDrawer";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { useOrderRecoRefreshFollowup } from "@/lib/inventory/useOrderRecoRefreshFollowup";

/** ~10 body rows visible; header sticky inside the scrollport. */
const TABLE_MAX_H = "max-h-[min(36rem,70vh)]";

function BaseCell({ row }: { row: UsageDayAuditRow | undefined }) {
  if (!row) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  return (
    <div className="flex min-h-10 min-w-[6.5rem] flex-col items-start gap-0.5 px-1.5 py-1">
      <span className="text-sm font-medium tabular-nums">{formatQty(row.qty)}</span>
      <Badge
        variant={matrixChipVariant(row)}
        className="max-w-full truncate text-[10px] leading-tight font-normal"
      >
        {matrixStatusTag(row)}
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
  // Gate: client files that call *Action must import useConsoleAction.
  useConsoleAction();
  const { banner: recoBanner, followOrderReco } = useOrderRecoRefreshFollowup({
    pendingBanner:
      "Order recommendation refreshing — Avg/day and Order tubs update when ready.",
    doneToast: "Averages updated",
  });

  const onApplied = useCallback(
    (result: UsageDayOverridesApplied) => {
      followOrderReco({
        queued: result.queued ? ["order-reco"] : undefined,
        baselineRefreshedAt: result.baselineRefreshedAt,
      });
    },
    [followOrderReco],
  );

  if (!matrix.dates.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No closing readings in the last 30 days.
      </p>
    );
  }

  return (
    <>
      {recoBanner ? (
        <p
          className="mb-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
          data-testid="usage-day-reco-banner"
        >
          {recoBanner}
        </p>
      ) : null}

      <div
        className={`max-w-full min-w-0 overflow-auto rounded-lg border border-border ${TABLE_MAX_H}`}
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
        onApplied={onApplied}
      />
    </>
  );
}
