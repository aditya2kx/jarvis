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
import {
  formatDelta,
  formatQty,
  formatThresholdImpact,
  matrixChipVariant,
  matrixStatusTag,
  thresholdImpactForDraft,
  type OverrideDraftChoice,
} from "@/lib/inventory/usageDayAudit";
import { applyUsageDayOverridesAction } from "@/app/inventory/actions";

function seedChoice(row: UsageDayAuditRow): OverrideDraftChoice {
  if (row.override_mode === "force_include") return "force_include";
  if (row.override_mode === "force_exclude") return "force_exclude";
  return "rule";
}

/**
 * Right-side editor for one closing date — same Sheet pattern as Restock /
 * Goals (mount only while open so closed state never reserves layout).
 * Draft locally; write only on Apply.
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

  // Critical: do not keep Sheet/Portal mounted when closed — Base UI can leave
  // a right-side panel / overlay that steals viewport width (Issue #194).
  if (!open || !date) return null;

  return (
    <Sheet
      open
      onOpenChange={(next) => {
        onOpenChange(next);
      }}
    >
      {/* Match RestockImportDrawer / GoalsDrawer SheetContent classes. */}
      <SheetContent className="w-full max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Usage overrides · {date}</SheetTitle>
          <SheetDescription>
            Nothing saves until Apply. Use the dropdown to count a day in (or keep
            it out of) the usage average — the preview updates as you change it.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-4 px-4 pb-2">
          {!dayRows.length ? (
            <p className="text-sm text-muted-foreground">No readings for this date.</p>
          ) : (
            dayRows.map((r) => {
              const choice = choices[r.item] ?? seedChoice(r);
              const itemRows = rows.filter((x) => x.item === r.item);
              const impact = thresholdImpactForDraft(itemRows, r.submitted_date, choice);
              const dirtyChoice = choice !== seedChoice(r);
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
                    <Badge variant={matrixChipVariant(r)} className="shrink-0 text-[10px] font-normal">
                      {matrixStatusTag(r)}
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
                        <SelectTrigger className="h-9 w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="rule">Follow automatic rule</SelectItem>
                          <SelectItem value="force_include">Count this day in the average</SelectItem>
                          <SelectItem value="force_exclude">Keep this day out of the average</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  ) : (
                    <p className="text-xs text-muted-foreground">Read-only (flag off).</p>
                  )}
                  <pre
                    className={`mt-2 whitespace-pre-wrap font-sans text-[11px] leading-snug text-muted-foreground ${
                      dirtyChoice ? "text-foreground/80" : ""
                    }`}
                  >
                    {formatThresholdImpact(impact)}
                  </pre>
                </div>
              );
            })
          )}
        </div>

        <SheetFooter className="gap-2">
          {(stage || error) && (
            <p
              className={`mr-auto text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}
            >
              {error || stage}
            </p>
          )}
          <div className="flex w-full justify-end gap-2">
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
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
