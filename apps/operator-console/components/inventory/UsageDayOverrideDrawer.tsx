"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import type { UsageDayAuditRow } from "@/lib/bq/queries";
import { formatDelta, formatQty, statusTag } from "@/lib/inventory/usageDayAudit";
import { applyUsageDayOverridesAction } from "@/app/inventory/actions";
import type { UsageDayOverrideMode } from "@/lib/bq/writes";

export type OverrideDraftChoice = "rule" | UsageDayOverrideMode;

function seedChoice(row: UsageDayAuditRow): OverrideDraftChoice {
  if (row.override_mode === "force_include") return "force_include";
  if (row.override_mode === "force_exclude") return "force_exclude";
  return "rule";
}

function chipVariant(
  row: UsageDayAuditRow,
): "default" | "secondary" | "destructive" | "outline" {
  if (row.override_mode === "force_include") return "default";
  if (row.override_mode === "force_exclude") return "destructive";
  if (row.status === "included") return "secondary";
  return "outline";
}

/**
 * Right-side editor for one closing date — draft locally, write only on Apply
 * (same confirm pattern as Restock / Tip Exemptions).
 */
export function UsageDayOverrideDrawer({
  open,
  onOpenChange,
  date,
  rows,
  writable,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  date: string | null;
  rows: UsageDayAuditRow[];
  writable: boolean;
}) {
  const dayRows = useMemo(
    () =>
      [...rows]
        .filter((r) => r.submitted_date === date)
        .sort((a, b) => a.item.localeCompare(b.item)),
    [rows, date],
  );

  const [choices, setChoices] = useState<Record<string, OverrideDraftChoice>>({});
  const { isPending, stage, error, run } = useConsoleAction();

  useEffect(() => {
    if (!open || !date) return;
    const init: Record<string, OverrideDraftChoice> = {};
    for (const r of dayRows) init[r.item] = seedChoice(r);
    setChoices(init);
  }, [open, date, dayRows]);

  const dirty = dayRows.filter((r) => {
    const choice = choices[r.item] ?? seedChoice(r);
    return choice !== seedChoice(r);
  });

  async function handleApply() {
    if (!date || !writable || !dirty.length) return;
    const changes = dirty.map((r) => ({
      item: r.item,
      mode: (choices[r.item] ?? "rule") as OverrideDraftChoice,
    }));
    const ack = await run(() => applyUsageDayOverridesAction(date, changes), {
      saving: "Applying…",
      done: "Overrides saved.",
      queued: "Overrides saved — averages updating…",
    });
    if (ack.ok) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full max-w-md flex-col overflow-hidden sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>Usage overrides · {date ?? "—"}</SheetTitle>
          <SheetDescription>
            Review each base for this closing date. Nothing writes until you Apply.
            Force include feeds the average; force exclude keeps it out.
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 pb-2">
          {!dayRows.length ? (
            <p className="text-sm text-muted-foreground">No readings for this date.</p>
          ) : (
            dayRows.map((r) => {
              const choice = choices[r.item] ?? seedChoice(r);
              return (
                <div
                  key={r.item}
                  className="rounded-lg border border-border px-3 py-2.5"
                >
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div>
                      <p className="font-medium">{r.item}</p>
                      <p className="text-xs text-muted-foreground">
                        Qty {formatQty(r.qty)}
                        {r.delta != null ? ` · Δ ${formatDelta(r.delta)}` : ""}
                      </p>
                    </div>
                    <Badge variant={chipVariant(r)} className="shrink-0 text-[10px]">
                      {statusTag(r)}
                    </Badge>
                  </div>
                  {writable ? (
                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs text-muted-foreground">Override</Label>
                      <Select
                        value={choice}
                        onValueChange={(v) => {
                          if (v == null) return;
                          setChoices((prev) => ({
                            ...prev,
                            [r.item]: v as OverrideDraftChoice,
                          }));
                        }}
                      >
                        <SelectTrigger className="h-9">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="rule">Follow rule (default)</SelectItem>
                          <SelectItem value="force_include">Force include in avg</SelectItem>
                          <SelectItem value="force_exclude">Force exclude from avg</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">Read-only (flag off).</p>
                  )}
                </div>
              );
            })
          )}
        </div>

        <SheetFooter className="gap-2 border-t border-border pt-3">
          {(stage || error) && (
            <p
              className={`mr-auto text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}
            >
              {error || stage}
            </p>
          )}
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            Cancel
          </Button>
          {writable ? (
            <Button
              onClick={() => void handleApply()}
              disabled={isPending || dirty.length === 0}
            >
              {isPending ? "Applying…" : dirty.length ? `Apply (${dirty.length})` : "Apply"}
            </Button>
          ) : null}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
