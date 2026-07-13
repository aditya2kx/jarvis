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
import type { HealthScorecard as HealthScorecardData, HealthMetric, HealthGroup } from "@/lib/kpi/health";

const STATUS_LABEL: Record<string, string> = {
  "on-track": "On track",
  "at-risk": "At risk",
  "off-track": "Off track",
  "no-goal": "No goal set",
};

// Hierarchical Goal and Tracking (Issue #158): section headers inspired by
// Stripe Dashboard / Linear Insights — groups without nested card chrome.
// Mobile: label+badge / value+bar / goal stacked (no overflow strip).
export function HealthScorecard({ data }: { data: HealthScorecardData }) {
  const groups: HealthGroup[] = data.groups?.length
    ? data.groups
    : [{ id: "finance", label: "", metrics: data.metrics }];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Goal and Tracking — {data.windowLabel}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {groups.map((g) => (
          <section key={g.id} className="flex flex-col">
            {g.label ? (
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">
                {g.label}
              </h3>
            ) : null}
            <div className="flex flex-col divide-y divide-border border-t border-border/60">
              {g.metrics.map((m) => (
                <MetricRow key={m.key} metric={m} />
              ))}
            </div>
          </section>
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

  const field = m.goalKey ? GOAL_FIELDS.find((f) => f.key === m.goalKey) : undefined;
  const editable = Boolean(field && m.goalKey);

  function startEdit() {
    if (!field) return;
    const raw = m.rawGoal ?? "";
    setInputValue(field.kind === "percent" ? fractionToPercentInput(raw) : raw);
    setSaveError(null);
    setEditing(true);
  }

  function save() {
    if (!field || !m.goalKey) return;
    const stored = field.kind === "percent" ? percentInputToFraction(inputValue) : inputValue;
    startTransition(async () => {
      try {
        await saveGoalAction(m.goalKey!, stored);
        setEditing(false);
        router.refresh();
      } catch (e) {
        setSaveError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="flex flex-col gap-2 py-3 pl-2 first:pt-3 last:pb-0 sm:pl-3">
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
        <span className="w-[5.5rem] shrink-0 text-base font-semibold tabular-nums sm:w-28 sm:text-lg">
          {m.formatted}
        </span>
        <GoalBar status={m.status} pace={m.pace} />
      </div>

      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {editable && editing && field ? (
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
            {editable ? (
              <button type="button" aria-label={`Edit ${m.label} goal`} onClick={startEdit} className="text-muted-foreground/70 hover:text-foreground">
                <PencilIcon className="size-3" />
              </button>
            ) : null}
          </>
        )}
      </div>
      {saveError ? <p className="text-xs text-destructive">Save failed: {saveError}</p> : null}
    </div>
  );
}
