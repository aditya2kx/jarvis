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
import { GOAL_FIELDS, fractionToPercentInput, percentInputToFraction, sanitizeDollarInput } from "@/lib/kpi/goal-fields";
import type { HealthScorecard as HealthScorecardData, HealthMetric } from "@/lib/kpi/health";

const STATUS_LABEL: Record<string, string> = {
  "on-track": "On track",
  "at-risk": "At risk",
  "off-track": "Off track",
  "no-goal": "No goal set",
};

// Mobile-first layout (Issue #158 operator feedback): label+badge on row 1,
// actual + bar on row 2, goal edit on row 3 — never squeeze value/bar/goal/badge
// into one overflowing horizontal strip at ~390px.
export function HealthScorecard({ data }: { data: HealthScorecardData }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Goal and Tracking — {data.windowLabel}
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
    <div className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1 text-sm text-muted-foreground">
          <span className="truncate">{m.label}</span>
          <Tooltip>
            <TooltipTrigger
              render={
                <button type="button" aria-label={`About ${m.label}`} className="shrink-0 text-muted-foreground/70 hover:text-foreground">
                  <InfoIcon className="size-3.5" />
                </button>
              }
            />
            <TooltipContent>{m.info}</TooltipContent>
          </Tooltip>
        </span>
        <Badge
          className="shrink-0"
          variant={m.status === "on-track" ? "default" : m.status === "no-goal" ? "secondary" : "destructive"}
        >
          {STATUS_LABEL[m.status]}
        </Badge>
      </div>

      <div className="flex min-w-0 items-center gap-2 sm:gap-3">
        <span className="w-[4.5rem] shrink-0 text-base font-semibold tabular-nums sm:w-24 sm:text-lg">
          {m.formatted}
        </span>
        <GoalBar status={m.status} pace={m.pace} />
      </div>

      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {editing ? (
          <div className="flex items-center gap-1">
            <div className="relative w-24">
              <Input
                autoFocus
                type="text"
                inputMode="decimal"
                value={inputValue}
                onChange={(e) =>
                  setInputValue(field.kind === "dollars" ? sanitizeDollarInput(e.target.value) : e.target.value)
                }
                className={
                  field.kind === "percent" || field.kind === "minutes"
                    ? "h-7 pr-7 text-xs"
                    : field.kind === "dollars"
                      ? "h-7 pl-4 text-xs"
                      : "h-7 text-xs"
                }
              />
              {field.kind === "dollars" ? (
                <span className="pointer-events-none absolute inset-y-0 left-1.5 flex items-center text-xs text-muted-foreground">$</span>
              ) : null}
              {field.kind === "percent" ? (
                <span className="pointer-events-none absolute inset-y-0 right-1.5 flex items-center text-xs text-muted-foreground">%</span>
              ) : null}
              {field.kind === "minutes" ? (
                <span className="pointer-events-none absolute inset-y-0 right-1.5 flex items-center text-xs text-muted-foreground">min</span>
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
          <>
            <span>goal {m.goalFormatted}</span>
            <button type="button" aria-label={`Edit ${m.label} goal`} onClick={startEdit} className="text-muted-foreground/70 hover:text-foreground">
              <PencilIcon className="size-3" />
            </button>
          </>
        )}
      </div>
      {saveError ? <p className="text-xs text-destructive">Save failed: {saveError}</p> : null}
    </div>
  );
}
