"use client";

import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { setCapacityAction } from "@/app/inventory/actions";

// Inline quick-edit for order_reco_max_tubs (store_config) — a single
// frequent numeric edit, so an inline input fits better than a drawer (see
// PLAN.md write-UX pattern: hybrid inline/drawer/modal by write frequency).
export function CapacityEdit({ currentMaxTubs }: { currentMaxTubs?: number }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(String(currentMaxTubs ?? 120));
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  if (!editing) {
    return (
      <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
        Capacity: {currentMaxTubs ?? "—"} tubs
      </Button>
    );
  }

  return (
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
        onClick={() =>
          startTransition(async () => {
            const n = Number(value);
            if (Number.isNaN(n) || n < 0) {
              setError("Enter a non-negative number.");
              return;
            }
            setError(null);
            await setCapacityAction(n);
            setEditing(false);
          })
        }
      >
        Save
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
        Cancel
      </Button>
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </div>
  );
}
