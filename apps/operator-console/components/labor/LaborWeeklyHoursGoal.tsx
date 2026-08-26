"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CheckIcon, PencilIcon, XIcon } from "lucide-react";
import { saveGoalAction } from "@/app/home/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";
import { parseHoursGoalInput } from "@/lib/kpi/goal-fields";

/**
 * Compact header control for `store_config.goal_labor_hours_week` — same
 * key as Home Labor scorecard. Matches FilterSelect label + h-7 trigger.
 */
export function LaborWeeklyHoursGoal({
  current,
}: {
  current: number | undefined;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const { isPending, error, run, setError } = useConsoleAction();

  const display =
    current != null && !Number.isNaN(Number(current))
      ? Number(current).toLocaleString("en-US", { maximumFractionDigits: 1 })
      : "—";

  function startEdit() {
    setInputValue(current != null && !Number.isNaN(Number(current)) ? String(current) : "");
    setError(null);
    setEditing(true);
  }

  function save() {
    const stored = parseHoursGoalInput(inputValue);
    if (stored == null) {
      setError("Enter hours > 0");
      return;
    }
    void run(() => saveGoalAction("goal_labor_hours_week", stored), {
      saving: "Saving…",
    }).then((ack) => {
      if (ack.ok) {
        setEditing(false);
        router.refresh();
      }
    });
  }

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">Hours goal</span>
      {editing ? (
        <div className="flex items-center gap-0.5">
          <div className="relative w-[4.75rem]">
            <Input
              autoFocus
              type="text"
              inputMode="decimal"
              aria-label="Weekly labor hours goal"
              aria-invalid={error ? true : undefined}
              value={inputValue}
              onChange={(e) => {
                setInputValue(e.target.value);
                setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") setEditing(false);
              }}
              className="h-7 pr-7 text-xs"
            />
            <span className="pointer-events-none absolute inset-y-0 right-1.5 flex items-center text-[10px] text-muted-foreground">
              hrs
            </span>
          </div>
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={isPending}
            onClick={save}
            aria-label="Save weekly hours goal"
            className="size-7"
          >
            <CheckIcon className="size-3.5" />
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={isPending}
            onClick={() => setEditing(false)}
            aria-label="Cancel weekly hours goal edit"
            className="size-7"
          >
            <XIcon className="size-3.5" />
          </Button>
        </div>
      ) : (
        <button
          type="button"
          aria-label="Edit weekly hours goal"
          onClick={startEdit}
          className="inline-flex h-7 items-center gap-1 rounded-md border border-input bg-transparent px-2 text-xs font-medium tabular-nums text-foreground hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {display} hrs
          <PencilIcon className="size-3 text-muted-foreground/70" />
        </button>
      )}
      {error ? (
        <span className="max-w-[8rem] truncate text-[10px] text-destructive" title={error}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
