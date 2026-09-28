"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { setCapacityAction } from "@/app/inventory/actions";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { useOrderRecoRefreshFollowup } from "@/lib/inventory/useOrderRecoRefreshFollowup";

// Inline quick-edit for order_reco_max_tubs (store_config) — a single
// frequent numeric edit, so an inline input fits better than a drawer (see
// PLAN.md write-UX pattern: hybrid inline/drawer/modal by write frequency).
export function CapacityEdit({ currentMaxTubs }: { currentMaxTubs?: number }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(currentMaxTubs ?? 120));
  const { isPending, stage, error, run, setError } = useConsoleAction();
  // Progress shows in the page-level OrderRecoStatusBanner, not inline here.
  const { followOrderReco } = useOrderRecoRefreshFollowup();

  if (!editing) {
    return (
      <div className="flex flex-col items-end gap-1">
        <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
          Capacity: {currentMaxTubs ?? "—"} tubs
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1.5">
        <Input
          type="number"
          min={0}
          className="w-20"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button
          size="sm"
          disabled={isPending}
          onClick={() => {
            const n = Number(value);
            if (Number.isNaN(n) || n < 0) {
              setError("Enter a non-negative number.");
              return;
            }
            void run(() => setCapacityAction(n), {
              saving: "Saving…",
              queued: "Capacity saved — recommendation refreshing…",
              done: "Capacity saved.",
            }).then((ack) => {
              if (!ack.ok) return;
              setEditing(false);
              followOrderReco({
                queued: ack.queued,
                data: ack.data,
              });
            });
          }}
        >
          {isPending ? "Saving…" : "Save"}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
          Cancel
        </Button>
        {stage || error ? (
          <span className={`text-xs ${error ? "text-destructive" : "text-muted-foreground"}`}>
            {stage || error}
          </span>
        ) : null}
      </div>
    </div>
  );
}
