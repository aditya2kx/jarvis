"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { InfoIcon, PencilIcon, CheckIcon, XIcon, ChevronRightIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { GoalBar } from "./GoalBar";
import { saveGoalAction } from "@/app/home/actions";
import { GOAL_FIELDS, fractionToPercentInput, percentInputToFraction, sanitizeDollarInput } from "@/lib/kpi/goal-fields";
import { periodHref } from "@/lib/filters/range";
import type { HealthScorecard as HealthScorecardData, HealthMetric, HealthGroup, GoalStatus } from "@/lib/kpi/health";

const STATUS_LABEL: Record<GoalStatus, string> = {
  "on-track": "On track",
  "at-risk": "At risk",
  "off-track": "Off track",
  "no-goal": "No goal set",
};

const OVERALL_LABEL: Record<GoalStatus, string> = {
  "on-track": "Healthy",
  "at-risk": "At risk",
  "off-track": "Needs attention",
  "no-goal": "Set goals to track",
};

export function HealthScorecard({ data }: { data: HealthScorecardData }) {
  const groups: HealthGroup[] = data.groups?.length
    ? data.groups
    : [{ id: "finance", label: "", href: "/home", metrics: data.metrics }];
  const overall = data.overallStatus ?? "no-goal";

  return (
    <Card>
      <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-1">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Goal and Tracking — {data.windowLabel}
          </CardTitle>
          <p className="text-lg font-semibold tracking-tight sm:text-xl">
            Palmetto health:{" "}
            <span
              className={
                overall === "on-track"
                  ? "text-foreground"
                  : overall === "no-goal"
                    ? "text-muted-foreground"
                    : "text-destructive"
              }
            >
              {OVERALL_LABEL[overall]}
            </span>
          </p>
        </div>
        <Badge
          className="w-fit shrink-0 text-sm"
          variant={overall === "on-track" ? "default" : overall === "no-goal" ? "secondary" : "destructive"}
        >
          {STATUS_LABEL[overall]}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {groups.map((g) => (
          <section key={g.id} className="flex flex-col">
            {g.label ? (
              <Link
                href={periodHref(g.href, data.win.preset)}
                className="mb-2 flex items-center justify-between gap-2 rounded-md border border-border bg-muted/50 px-3 py-2.5 text-sm font-medium text-foreground transition-colors hover:border-foreground/25 hover:bg-muted"
              >
                <span>{g.label}</span>
                <span className="flex items-center gap-0.5 text-xs font-normal text-muted-foreground">
                  Open
                  <ChevronRightIcon className="size-3.5" />
                </span>
              </Link>
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
        <GoalBar
          status={m.status}
          pace={m.pace}
          actual={m.actual}
          goal={m.goal}
          goalLabel={m.goalFormatted}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
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
            {m.deltaFormatted ? (
              <span className="text-muted-foreground/90">· {m.deltaFormatted}</span>
            ) : null}
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
