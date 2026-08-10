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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { useOrderRecoRefreshFollowup } from "@/lib/inventory/useOrderRecoRefreshFollowup";
import {
  applyOrderTubOverridesAction,
  submitRestockAction,
} from "@/app/inventory/actions";

export type EstimateTubRow = {
  item: string;
  orderTubs: number;
  source: "Estimated" | "Manual" | "Actuals" | null;
};

type Mode = "estimated" | "manual";

type Draft = { mode: Mode; qty: string };

/**
 * Batch editor for one delivery date's Order Tubs.
 * - Estimated/Manual → pin overrides (Issue #225).
 * - Actuals → replace-per-date restock orders (Issue #238).
 */
export function EstimateTubsDrawer({
  open,
  onOpenChange,
  deliveryDate,
  rows,
  maxTubs,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  deliveryDate: string | null;
  rows: EstimateTubRow[];
  maxTubs?: number;
}) {
  const bases = useMemo(
    () =>
      rows
        .filter((r) => r.item !== "TOTAL" && r.item !== "Blade")
        .sort((a, b) => a.item.localeCompare(b.item)),
    [rows],
  );

  const isActuals = bases.some((r) => r.source === "Actuals");

  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const { isPending, stage, error, run, setError } = useConsoleAction();
  const { banner, followOrderReco } = useOrderRecoRefreshFollowup({
    pendingBanner: isActuals
      ? "Order recommendation refreshing — Actuals update when ready."
      : "Order recommendation refreshing — Order tubs update when pins apply.",
    doneToast: isActuals
      ? "Actuals saved — Order tubs updated"
      : "Estimate pins applied — Order tubs updated",
  });

  useEffect(() => {
    if (!open || !deliveryDate) return;
    const init: Record<string, Draft> = {};
    for (const r of bases) {
      init[r.item] = {
        mode: isActuals ? "manual" : r.source === "Manual" ? "manual" : "estimated",
        qty: String(r.orderTubs ?? 0),
      };
    }
    setDrafts(init);
    setError(null);
  }, [open, deliveryDate, bases, isActuals, setError]);

  function seed(item: string): Draft {
    const r = bases.find((b) => b.item === item);
    return {
      mode: isActuals ? "manual" : r?.source === "Manual" ? "manual" : "estimated",
      qty: String(r?.orderTubs ?? 0),
    };
  }

  const dirty = bases.filter((r) => {
    const d = drafts[r.item] ?? seed(r.item);
    if (isActuals) {
      return Number(d.qty) !== r.orderTubs;
    }
    const wasManual = r.source === "Manual";
    if (d.mode === "estimated") return wasManual;
    if (!wasManual) return true;
    return Number(d.qty) !== r.orderTubs;
  });

  async function handleApply() {
    if (!deliveryDate || !dirty.length) return;

    if (isActuals) {
      const actualRows: { item: string; quantityTubs: number }[] = [];
      for (const r of bases) {
        const d = drafts[r.item] ?? seed(r.item);
        const n = Number(d.qty);
        if (!Number.isInteger(n) || n < 0) {
          setError(`Enter a non-negative integer for ${r.item}.`);
          return;
        }
        actualRows.push({ item: r.item, quantityTubs: n });
      }
      const ack = await run(
        () => submitRestockAction(deliveryDate, "add-order", actualRows),
        {
          saving: "Saving…",
          done: "Actuals saved.",
          queued: "Actuals saved — recommendation refreshing…",
        },
      );
      if (!ack.ok) return;
      onOpenChange(false);
      followOrderReco({
        queued: ack.queued,
        baselineRefreshedAt: ack.data?.baselineRefreshedAt ?? null,
      });
      return;
    }

    const manualRows: { item: string; quantityTubs: number }[] = [];
    for (const r of bases) {
      const d = drafts[r.item] ?? seed(r.item);
      if (d.mode !== "manual") continue;
      const n = Number(d.qty);
      if (!Number.isInteger(n) || n < 0) {
        setError(`Enter a non-negative integer for ${r.item}.`);
        return;
      }
      manualRows.push({ item: r.item, quantityTubs: n });
    }
    if (maxTubs != null) {
      const sum = manualRows.reduce((a, r) => a + r.quantityTubs, 0);
      if (sum > maxTubs) {
        setError(`Manual pins sum (${sum}) exceeds capacity (${maxTubs}).`);
        return;
      }
    }

    const ack = await run(() => applyOrderTubOverridesAction(deliveryDate, manualRows), {
      saving: "Saving…",
      done: "Pins saved.",
      queued: "Pins saved — recommendation refreshing…",
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
  if (!open || !deliveryDate) return null;

  return (
    <Sheet
      open
      onOpenChange={(next) => {
        onOpenChange(next);
      }}
    >
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>
            {isActuals ? "Edit actuals" : "Edit estimates"} — {deliveryDate}
          </SheetTitle>
          <SheetDescription>
            {isActuals ? (
              <>
                Update Actuals Order Tubs for this delivery, then Apply once. Saves replace the
                uploaded Actuals for the date (same as Restock → Add actuals).
              </>
            ) : (
              <>
                Pin Order Tubs per base for this Estimated date, then Apply once. Unpinned bases
                recompute under capacity{maxTubs != null ? ` (${maxTubs} tubs)` : ""}.
              </>
            )}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
          {bases.map((r) => {
            const d = drafts[r.item] ?? seed(r.item);
            return (
              <div
                key={r.item}
                className="flex flex-wrap items-end gap-2 rounded-md border border-border/60 p-2"
              >
                <div className="min-w-[7rem] flex-1">
                  <Label className="text-xs text-muted-foreground">{r.item}</Label>
                  <p className="text-xs text-muted-foreground">
                    Current: {r.orderTubs}
                    {isActuals
                      ? " · Actuals"
                      : r.source === "Manual"
                        ? " · Manual"
                        : " · Estimated"}
                  </p>
                </div>
                {isActuals ? null : (
                  <div className="w-[8.5rem]">
                    <Label className="text-xs text-muted-foreground">Mode</Label>
                    <Select
                      value={d.mode}
                      onValueChange={(v) => {
                        if (v !== "estimated" && v !== "manual") return;
                        setDrafts((prev) => ({
                          ...prev,
                          [r.item]: { ...d, mode: v },
                        }));
                      }}
                    >
                      <SelectTrigger className="h-10">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="estimated">Estimated</SelectItem>
                        <SelectItem value="manual">Manual</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}
                <div className="w-20">
                  <Label className="text-xs text-muted-foreground">Tubs</Label>
                  <Input
                    type="number"
                    min={0}
                    step={1}
                    className="h-10"
                    disabled={!isActuals && d.mode !== "manual"}
                    aria-label={`${r.item} tubs`}
                    value={d.qty}
                    onChange={(e) =>
                      setDrafts((prev) => ({
                        ...prev,
                        [r.item]: { ...d, qty: e.target.value },
                      }))
                    }
                  />
                </div>
              </div>
            );
          })}
        </div>

        <SheetFooter className="gap-2 border-t border-border/60 pt-3">
          {banner ? (
            <p className="w-full text-xs text-amber-800 dark:text-amber-200">{banner}</p>
          ) : null}
          {stage || error ? (
            <p className={`w-full text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
              {error || stage}
            </p>
          ) : null}
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
            Cancel
          </Button>
          <Button onClick={() => void handleApply()} disabled={isPending || !dirty.length}>
            {isPending ? "Saving…" : `Apply${dirty.length ? ` (${dirty.length})` : ""}`}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
