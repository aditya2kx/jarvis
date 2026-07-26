"use client";

import { useMemo, useState, useTransition } from "react";
import { Badge } from "@/components/ui/badge";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { UsageDayAuditRow } from "@/lib/bq/queries";
import {
  formatDelta,
  groupUsageDayAudit,
  previewLine,
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

function Chip({
  row,
  writable,
  onDone,
}: {
  row: UsageDayAuditRow;
  writable: boolean;
  onDone: (msg: string) => void;
}) {
  const { run, isPending } = useConsoleAction();
  const label = `${row.item} ${formatDelta(row.delta)}`;
  const reason = row.reason && row.status !== "included" ? ` · ${row.reason}` : "";
  const title = writable
    ? `Tap to cycle override (${row.override_mode ?? "rule"}). ${row.reason ?? ""}`
    : `${row.reason ?? row.status}`;

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
      className="min-h-11 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
    >
      <Badge variant={chipVariant(row)} className="max-w-full truncate text-left">
        {label}
        {reason ? (
          <span className="ml-1 font-normal opacity-80">{reason}</span>
        ) : null}
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
  const groups = useMemo(() => groupUsageDayAudit(rows), [rows]);
  const [previewByDate, setPreviewByDate] = useState<Record<string, string>>({});
  const [, startTransition] = useTransition();

  if (!groups.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No closing readings in the last 30 days.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border" data-testid="usage-day-audit">
      <table className="w-full min-w-[36rem] text-left text-sm">
        <thead className="border-b border-border bg-muted/40">
          <tr>
            <th className="sticky left-0 z-10 bg-muted/40 px-3 py-2 font-medium">Date</th>
            <th className="px-3 py-2 font-medium">Included (Δ)</th>
            <th className="px-3 py-2 font-medium">Excluded (Δ · why)</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <tr key={g.date} className="border-b border-border/60 align-top">
              <td className="sticky left-0 z-10 bg-background px-3 py-3 font-medium whitespace-nowrap">
                {g.date}
                {previewByDate[g.date] ? (
                  <p className="mt-1 max-w-[10rem] text-xs font-normal text-muted-foreground">
                    {previewByDate[g.date]}
                  </p>
                ) : null}
              </td>
              <td className="px-3 py-3">
                <div className="flex flex-wrap gap-1.5">
                  {g.included.length ? (
                    g.included.map((r) => (
                      <Chip
                        key={`${r.item}-${r.submitted_date}`}
                        row={r}
                        writable={writable}
                        onDone={(msg) =>
                          startTransition(() =>
                            setPreviewByDate((prev) => ({ ...prev, [g.date]: msg })),
                          )
                        }
                      />
                    ))
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </div>
              </td>
              <td className="px-3 py-3">
                <div className="flex flex-wrap gap-1.5">
                  {g.excluded.length ? (
                    g.excluded.map((r) => (
                      <Chip
                        key={`${r.item}-${r.submitted_date}`}
                        row={r}
                        writable={writable}
                        onDone={(msg) =>
                          startTransition(() =>
                            setPreviewByDate((prev) => ({ ...prev, [g.date]: msg })),
                          )
                        }
                      />
                    ))
                  ) : (
                    <span className="text-xs text-muted-foreground">—</span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
