"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { InfoIcon, PencilIcon, CheckIcon, XIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { GoalBar } from "./GoalBar";
import { saveGoalAction } from "@/app/home/actions";
import { GOAL_FIELDS, fractionToPercentInput, percentInputToFraction } from "@/lib/kpi/goal-fields";
import type { HealthScorecard as HealthScorecardData, HealthMetric } from "@/lib/kpi/health";

const STATUS_LABEL: Record<string, string> = {
  "on-track": "On track",
  "at-risk": "At risk",
  "off-track": "Off track",
  "no-goal": "No goal set",
};

// The window itself is now the page's own `?range=` search param (see
// app/home/page.tsx's `Period` FilterSelect in the PageHeader) — this used
// to hold its own weekly/monthly toggle state, pre-fetching both windows
// server-side; that segmented control didn't fit well at 390px and
// duplicated the Period control every other Performance screen already
// has, so it's retired in favor of the one shared Period control.
export function HealthScorecard({ data }: { data: HealthScorecardData }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Operational health — {data.windowLabel}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col divide-y divide-border">
        {data.metrics.map((m) => (
          <MetricRow key={m.key} metric={m} />
        ))}
      </CardContent>
    </Card>
  );
}

function MetricRow({ metric: m }: { metric: HealthMetric }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [inputValue, setInputValue] = useState("");
  const [isPending, startTransition] = useTransition();
  const [saveError, setSaveError] = useState<string | null>(null);

  const field = GOAL_FIELDS.find((f) => f.key === m.goalKey)!;

  function startEdit() {
    const raw = m.rawGoal ?? "";
    setInputValue(field.kind === "percent" ? fractionToPercentInput(raw) : raw);
    setSaveError(null);
    setEditing(true);
  }

  function save() {
    const stored = field.kind === "percent" ? percentInputToFraction(inputValue) : inputValue;
    startTransition(async () => {
      try {
        await saveGoalAction(m.goalKey, stored);
        setEditing(false);
        router.refresh();
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
        <span className="flex shrink-0 items-center gap-1 text-sm text-muted-foreground sm:w-40">
          {m.label}
          <Tooltip>
            <TooltipTrigger
              render={
                <button type="button" aria-label={`About ${m.label}`} className="text-muted-foreground/70 hover:text-foreground">
                  <InfoIcon className="size-3.5" />
                </button>
              }
            />
            <TooltipContent>{m.info}</TooltipContent>
          </Tooltip>
        </span>
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="w-16 shrink-0 text-lg font-semibold sm:w-24">{m.formatted}</span>
          <GoalBar status={m.status} pace={m.pace} />
          {editing ? (
            <div className="flex shrink-0 items-center gap-1">
              <div className="relative w-20">
                <Input
                  autoFocus
                  type="text"
                  inputMode="decimal"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  className={field.kind === "percent" ? "h-7 pr-5 text-xs" : field.kind === "dollars" ? "h-7 pl-4 text-xs" : "h-7 text-xs"}
                />
                {field.kind === "dollars" ? (
                  <span className="pointer-events-none absolute inset-y-0 left-1.5 flex items-center text-xs text-muted-foreground">$</span>
                ) : null}
                {field.kind === "percent" ? (
                  <span className="pointer-events-none absolute inset-y-0 right-1.5 flex items-center text-xs text-muted-foreground">%</span>
                ) : null}
              </div>
              <Button size="icon-sm" variant="ghost" disabled={isPending} onClick={save} aria-label="Save goal">
                <CheckIcon className="size-3.5" />
              </Button>
              <Button size="icon-sm" variant="ghost" disabled={isPending} onClick={() => setEditing(false)} aria-label="Cancel">
                <XIcon className="size-3.5" />
              </Button>
            </div>
          ) : (
            <span className="flex shrink-0 items-center gap-1 text-right text-xs text-muted-foreground sm:w-24">
              goal {m.goalFormatted}
              <button type="button" aria-label={`Edit ${m.label} goal`} onClick={startEdit} className="text-muted-foreground/70 hover:text-foreground">
                <PencilIcon className="size-3" />
              </button>
            </span>
          )}
        </div>
        <Badge
          variant={m.status === "on-track" ? "default" : m.status === "no-goal" ? "secondary" : "destructive"}
        >
          {STATUS_LABEL[m.status]}
        </Badge>
      </div>
      {saveError ? <p className="text-xs text-destructive">Save failed: {saveError}</p> : null}
    </div>
  );
}
