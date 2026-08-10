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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { useOrderRecoRefreshFollowup } from "@/lib/inventory/useOrderRecoRefreshFollowup";
import {
  applyCurrentQtyOverridesAction,
  clearCurrentQtyOverridesAction,
} from "@/app/inventory/actions";

export type CurrentQtyRow = {
  item: string;
  currentQty: number;
};

/**
 * Batch Current Qty editor for all bases (Issue #240) — same right Sheet pattern
 * as EstimateTubsDrawer / Order Tubs.
 */
export function CurrentQtyDrawer({
  open,
  onOpenChange,
  rows,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rows: CurrentQtyRow[];
}) {
  const bases = useMemo(
    () =>
      rows
        .filter((r) => r.item !== "TOTAL" && r.item !== "Blade")
        .sort((a, b) => a.item.localeCompare(b.item)),
    [rows],
  );

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const { isPending, stage, error, run, setError } = useConsoleAction();
  const { banner, followOrderReco } = useOrderRecoRefreshFollowup({
    pendingBanner: "Order recommendation refreshing — Current Qty updates when ready.",
    doneToast: "Current Qty updated",
  });

  useEffect(() => {
    if (!open) return;
    const init: Record<string, string> = {};
    for (const r of bases) {
      init[r.item] = String(r.currentQty ?? 0);
    }
    setDrafts(init);
    setError(null);
  }, [open, bases, setError]);

  function seed(item: string): string {
    const r = bases.find((b) => b.item === item);
    return String(r?.currentQty ?? 0);
  }

  const dirty = bases.filter((r) => {
    const q = Number(drafts[r.item] ?? seed(r.item));
    return Number.isFinite(q) && Math.abs(q - r.currentQty) > 1e-9;
  });

  async function handleApply() {
    if (!dirty.length) return;
    const payload: { item: string; quantityUnits: number }[] = [];
    for (const r of bases) {
      const raw = drafts[r.item] ?? seed(r.item);
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 0) {
        setError(`Enter a non-negative number for ${r.item}.`);
        return;
      }
      if (Math.abs(n - r.currentQty) > 1e-9) {
        payload.push({ item: r.item, quantityUnits: n });
      }
    }
    if (!payload.length) return;

    const ack = await run(() => applyCurrentQtyOverridesAction(payload), {
      saving: "Saving…",
      done: "Current Qty saved.",
      queued: "Current Qty saved — recommendation refreshing…",
    });
    if (!ack.ok) return;
    onOpenChange(false);
    followOrderReco({
      queued: ack.queued,
      baselineRefreshedAt: ack.data?.baselineRefreshedAt ?? null,
    });
  }

  async function handleResetAll() {
    const items = bases.map((r) => r.item);
    if (!items.length) return;
    const ack = await run(() => clearCurrentQtyOverridesAction(items), {
      saving: "Resetting…",
      done: "Current Qty reset to ClickUp readings.",
      queued: "Current Qty reset — recommendation refreshing…",
    });
    if (!ack.ok) return;
    onOpenChange(false);
    followOrderReco({
      queued: ack.queued,
      baselineRefreshedAt: ack.data?.baselineRefreshedAt ?? null,
    });
  }

  // Critical: do not keep Sheet mounted when closed — Base UI can leave a
  // right-side panel that steals viewport width (Issue #194).
  if (!open) return null;

  return (
    <Sheet
      open
      onOpenChange={(next) => {
        onOpenChange(next);
      }}
    >
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Edit Current Qty</SheetTitle>
          <SheetDescription>
            Override on-hand for each base, then Apply once. Sticky until you reset to ClickUp
            closings.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
          {bases.map((r) => {
            const qty = drafts[r.item] ?? seed(r.item);
            return (
              <div
                key={r.item}
                className="flex flex-wrap items-end gap-2 rounded-md border border-border/60 p-2"
              >
                <div className="min-w-[7rem] flex-1">
                  <Label className="text-xs text-muted-foreground">{r.item}</Label>
                  <p className="text-xs text-muted-foreground">Current: {formatQty(r.currentQty)}</p>
                </div>
                <div className="w-24">
                  <Label className="text-xs text-muted-foreground">Qty</Label>
                  <Input
                    type="number"
                    min={0}
                    step={0.1}
                    className="h-10 tabular-nums"
                    aria-label={`${r.item} Current Qty`}
                    value={qty}
                    disabled={isPending}
                    onChange={(e) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [r.item]: e.target.value,
                      }))
                    }
                  />
                </div>
              </div>
            );
          })}
        </div>

        <SheetFooter className="gap-2 border-t border-border/60 pt-3 sm:justify-between">
          {banner ? (
            <p className="w-full text-xs text-amber-800 dark:text-amber-200">{banner}</p>
          ) : null}
          {stage || error ? (
            <p className={`w-full text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
              {error || stage}
            </p>
          ) : null}
          <Button
            type="button"
            variant="outline"
            disabled={isPending || !bases.length}
            onClick={() => void handleResetAll()}
          >
            Reset all to ClickUp
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isPending}>
              Cancel
            </Button>
            <Button onClick={() => void handleApply()} disabled={isPending || !dirty.length}>
              {isPending ? "Saving…" : `Apply${dirty.length ? ` (${dirty.length})` : ""}`}
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function formatQty(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}
