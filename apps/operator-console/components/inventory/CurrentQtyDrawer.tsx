"use client";

import { useEffect, useState } from "react";
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
  clearCurrentQtyOverrideAction,
  setCurrentQtyOverrideAction,
} from "@/app/inventory/actions";

/**
 * Single-item Current Qty editor (Issue #240). Sticky override until Reset.
 */
export function CurrentQtyDrawer({
  open,
  onOpenChange,
  item,
  currentQty,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  item: string | null;
  currentQty: number | null;
}) {
  const [qty, setQty] = useState("");
  const { isPending, stage, error, run, setError } = useConsoleAction();
  const { banner, followOrderReco } = useOrderRecoRefreshFollowup({
    pendingBanner: "Order recommendation refreshing — Current Qty updates when ready.",
    doneToast: "Current Qty updated",
  });

  useEffect(() => {
    if (!open || !item) return;
    setQty(currentQty == null || Number.isNaN(currentQty) ? "" : String(currentQty));
    setError(null);
  }, [open, item, currentQty, setError]);

  const parsed = Number(qty);
  const dirty =
    item != null &&
    Number.isFinite(parsed) &&
    parsed >= 0 &&
    (currentQty == null || Math.abs(parsed - currentQty) > 1e-9);

  async function onSave() {
    if (!item || !dirty) return;
    const ack = await run(() => setCurrentQtyOverrideAction(item, parsed), {
      successToast: false,
    });
    if (!ack) return;
    await followOrderReco(ack);
    onOpenChange(false);
  }

  async function onReset() {
    if (!item) return;
    const ack = await run(() => clearCurrentQtyOverrideAction(item), {
      successToast: false,
    });
    if (!ack) return;
    await followOrderReco(ack);
    onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Edit Current Qty</SheetTitle>
          <SheetDescription>
            {item
              ? `Override on-hand for ${item}. Sticky until you reset to the ClickUp closing.`
              : "Pick a base."}
          </SheetDescription>
        </SheetHeader>
        {banner}
        {item ? (
          <div className="mt-4 space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="current-qty">Current Qty (tubs)</Label>
              <Input
                id="current-qty"
                type="number"
                min={0}
                step="0.1"
                inputMode="decimal"
                className="h-10 tabular-nums"
                value={qty}
                disabled={isPending}
                onChange={(e) => setQty(e.target.value)}
              />
            </div>
            {error ? (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            {stage ? (
              <p className="text-sm text-muted-foreground" aria-live="polite">
                {stage}
              </p>
            ) : null}
          </div>
        ) : null}
        <SheetFooter className="mt-auto gap-2 sm:justify-between">
          <Button
            type="button"
            variant="outline"
            disabled={!item || isPending}
            onClick={() => void onReset()}
          >
            Reset to ClickUp
          </Button>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              disabled={isPending}
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={!dirty || isPending}
              onClick={() => void onSave()}
            >
              {isPending ? "Saving…" : "Apply"}
            </Button>
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
