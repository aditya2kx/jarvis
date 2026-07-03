"use client";

import { useState, useTransition } from "react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { saveGoalsAction } from "@/app/home/actions";
import type { GoalKey } from "@/lib/bq/writes";

const GOAL_FIELDS: { key: GoalKey; label: string; unit: string }[] = [
  { key: "goal_net_sales_weekly", label: "Net sales — weekly target", unit: "$" },
  { key: "goal_net_sales_monthly", label: "Net sales — monthly target", unit: "$" },
  { key: "goal_labor_pct_max", label: "Labor % of net sales — max", unit: "fraction, e.g. 0.30" },
  { key: "goal_food_cost_pct_max", label: "Food cost % — max", unit: "fraction, e.g. 0.28" },
  { key: "goal_speed_on_time_pct_min", label: "On-time order speed — min", unit: "fraction, e.g. 0.90" },
  { key: "goal_inventory_runway_days_min", label: "Inventory runway — min days", unit: "days" },
];

// Nothing writes until Save — same confirm-before-write pattern as the
// Restock drawer (M3). Blank fields are left unset (per-key, not all-or-
// nothing) so an operator can fill in goals incrementally over time.
export function GoalsDrawer({ current }: { current: Partial<Record<GoalKey, string>> }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Partial<Record<GoalKey, string>>>(current);
  const [status, setStatus] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function handleSave() {
    startTransition(async () => {
      try {
        await saveGoalsAction(values);
        setStatus("Saved.");
        setOpen(false);
      } catch (e) {
        setStatus(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    });
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger render={<Button variant="outline" size="sm">Edit goals…</Button>} />
      <SheetContent className="w-full max-w-lg overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Goals</SheetTitle>
          <SheetDescription>
            Drives the on-track / at-risk / off-track status on the Home scorecard.
            Leave a field blank to leave that goal unset.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-4 px-4">
          {GOAL_FIELDS.map(({ key, label, unit }) => (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={key}>{label}</Label>
              <Input
                id={key}
                type="text"
                placeholder={unit}
                value={values[key] ?? ""}
                onChange={(e) => setValues((prev) => ({ ...prev, [key]: e.target.value }))}
              />
            </div>
          ))}

          {status ? <p className="text-sm text-muted-foreground">{status}</p> : null}
        </div>

        <SheetFooter>
          <Button onClick={handleSave} disabled={isPending}>
            {isPending ? "Saving…" : "Save goals"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
