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
import { GOAL_FIELDS, fractionToPercentInput, percentInputToFraction, sanitizeDollarInput } from "@/lib/kpi/goal-fields";
import type { GoalKey } from "@/lib/bq/writes";

// Percent-kind goals (labor %, food-cost %, on-time %) are STORED as
// fractions (0.15) — same unit health.ts/the Slack `/bhaga-cloud config set`
// path already reads/writes — but an operator naturally types a whole
// percent ("15", meaning 15%). Displaying/collecting the raw fraction
// caused the "I typed 15 and it showed 1500%" bug (store_config had a
// literal `15` where `0.15` belonged — see the one-time correction in
// PR #147 §4). `fractionToPercentInput`/`percentInputToFraction` are the
// single conversion point; every read/write of a percent field goes
// through them so the boundary can't drift.
function toInputValue(key: GoalKey, stored: string | undefined): string {
  if (stored == null) return "";
  const field = GOAL_FIELDS.find((f) => f.key === key)!;
  return field.kind === "percent" ? fractionToPercentInput(stored) : stored;
}

function toStoredValue(key: GoalKey, input: string): string {
  const field = GOAL_FIELDS.find((f) => f.key === key)!;
  return field.kind === "percent" ? percentInputToFraction(input) : input;
}

// Nothing writes until Save — same confirm-before-write pattern as the
// Restock drawer (M3). Blank fields are left unset (per-key, not all-or-
// nothing) so an operator can fill in goals incrementally over time.
export function GoalsDrawer({ current }: { current: Partial<Record<GoalKey, string>> }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Partial<Record<GoalKey, string>>>(
    Object.fromEntries(
      GOAL_FIELDS.map(({ key }) => [key, toInputValue(key, current[key])]),
    ) as Partial<Record<GoalKey, string>>,
  );
  const [status, setStatus] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function handleSave() {
    const stored = Object.fromEntries(
      GOAL_FIELDS.map(({ key }) => [key, values[key] ? toStoredValue(key, values[key]!) : values[key]]),
    ) as Partial<Record<GoalKey, string>>;
    startTransition(async () => {
      try {
        await saveGoalsAction(stored);
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
          {GOAL_FIELDS.map(({ key, label, kind, helpText }) => (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={key}>{label}</Label>
              <div className="relative">
                <Input
                  id={key}
                  type="text"
                  inputMode="decimal"
                  placeholder={helpText}
                  value={values[key] ?? ""}
                  onChange={(e) =>
                    setValues((prev) => ({
                      ...prev,
                      [key]: kind === "dollars" ? sanitizeDollarInput(e.target.value) : e.target.value,
                    }))
                  }
                  className={kind === "dollars" ? "pl-6" : kind === "percent" ? "pr-6" : undefined}
                />
                {kind === "dollars" ? (
                  <span className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-sm text-muted-foreground">
                    $
                  </span>
                ) : null}
                {kind === "percent" ? (
                  <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-sm text-muted-foreground">
                    %
                  </span>
                ) : null}
              </div>
              <p className="text-xs text-muted-foreground">{helpText}</p>
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
