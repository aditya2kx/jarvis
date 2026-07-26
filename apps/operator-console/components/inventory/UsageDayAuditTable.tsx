"use client";

import { useMemo, useState, useTransition } from "react";
import { Badge } from "@/components/ui/badge";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { UsageDayAuditRow } from "@/lib/bq/queries";
import {
  cellKey,
  formatQty,
  pivotUsageDayAudit,
  previewLine,
  statusTag,
} from "@/lib/inventory/usageDayAudit";
import {
  clearUsageDayOverrideAction,
  setUsageDayOverrideAction,
} from "@/app/inventory/actions";
import type { UsageDayOverrideMode } from "@/lib/bq/writes";

function chipVariant(
  row: UsageDayAuditRow,
): "default" | "secondary" | "destructive" | "outline" {
  if (row.override_mode === "force_include") return "default";
  if (row.override_mode === "force_exclude") return "destructive";
  if (row.status === "included") return "secondary";
  return "outline";
}

function nextMode(row: UsageDayAuditRow): UsageDayOverrideMode | "clear" {
  // Cycle: rule → force_exclude → force_include → clear
  if (!row.override_mode) {
    return row.status === "included" ? "force_exclude" : "force_include";
  }
  if (row.override_mode === "force_exclude") return "force_include";
  if (row.override_mode === "force_include") return "clear";
  return "clear";
}

function BaseCell({
  row,
  writable,
  onDone,
}: {
  row: UsageDayAuditRow | undefined;
  writable: boolean;
  onDone: (msg: string) => void;
}) {
  const { run, isPending } = useConsoleAction();

  if (!row) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  const qty = formatQty(row.qty);
  const tag = statusTag(row);
  const title = writable
    ? `Qty ${qty}. ${tag}. Tap to cycle override (${row.override_mode ?? "rule"}).`
    : `Qty ${qty}. ${tag}`;

  return (
    <button
      type="button"
      disabled={!writable || isPending}
      title={title}
      onClick={() => {
        if (!writable) return;
        const next = nextMode(row);
        const highBefore = row.high_bar;
        void run(async () => {
          if (next === "clear") {
            return clearUsageDayOverrideAction(row.item, row.submitted_date);
          }
          return setUsageDayOverrideAction(row.item, row.submitted_date, next);
        }).then((ack) => {
          if (ack && ack.ok && ack.data) {
            const d = ack.data as {
              high_bar?: number | null;
              similar_tomorrow_passes?: boolean | null;
              delta?: number | null;
            };
            onDone(
              previewLine({
                highBarBefore: highBefore,
                highBarAfter: d.high_bar,
                similarPasses: d.similar_tomorrow_passes,
                delta: d.delta ?? row.delta,
              }),
            );
          }
        });
      }}
      className="flex min-h-11 w-full min-w-[6.5rem] flex-col items-start gap-0.5 rounded-md px-1.5 py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
    >
      <span className="text-sm font-medium tabular-nums">{qty}</span>
      <Badge variant={chipVariant(row)} className="max-w-full truncate text-[10px] leading-tight">
        {tag}
      </Badge>
    </button>
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
  const [previewByDate, setPreviewByDate] = useState<Record<string, string>>({});
  const [, startTransition] = useTransition();

  if (!matrix.dates.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No closing readings in the last 30 days.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border" data-testid="usage-day-audit">
      <table className="w-max min-w-full border-collapse text-left text-sm">
        <thead className="border-b border-border bg-muted/40">
          <tr>
            <th className="sticky left-0 z-20 bg-muted/40 px-3 py-2 font-medium shadow-[2px_0_4px_-2px_rgba(0,0,0,0.15)]">
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
            <tr key={date} className="border-b border-border/60 align-top">
              <td className="sticky left-0 z-10 bg-background px-3 py-2 font-medium whitespace-nowrap shadow-[2px_0_4px_-2px_rgba(0,0,0,0.12)]">
                {date}
                {previewByDate[date] ? (
                  <p className="mt-1 max-w-[9rem] text-xs font-normal text-muted-foreground whitespace-normal">
                    {previewByDate[date]}
                  </p>
                ) : null}
              </td>
              {matrix.bases.map((base) => (
                <td key={base} className="px-1 py-1">
                  <BaseCell
                    row={matrix.cells.get(cellKey(date, base))}
                    writable={writable}
                    onDone={(msg) =>
                      startTransition(() =>
                        setPreviewByDate((prev) => ({ ...prev, [date]: msg })),
                      )
                    }
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
