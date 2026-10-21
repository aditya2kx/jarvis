"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { InfoIcon, PencilIcon, CheckIcon, XIcon, ChevronRightIcon } from "lucide-react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { GoalBar, STATUS_COLOR } from "./GoalBar";
import { saveGoalAction } from "@/app/home/actions";
import { GOAL_FIELDS, fractionToPercentInput, parseHoursGoalInput, percentInputToFraction, sanitizeDollarInput } from "@/lib/kpi/goal-fields";
import { periodHref, type RangePreset } from "@/lib/filters/range";
import type { HealthScorecard as HealthScorecardData, HealthMetric, HealthGroup, GoalStatus } from "@/lib/kpi/health";
import { useConsoleAction } from "@/lib/actions/useConsoleAction";

const STATUS_LABEL: Record<GoalStatus, string> = {
  "on-track": "On track",
  "at-risk": "At risk",
  "off-track": "Off track",
  "no-goal": "No goal",
};

const STATUS_TEXT: Record<GoalStatus, string> = {
  "on-track": "text-emerald-700 dark:text-emerald-400",
  "at-risk": "text-amber-700 dark:text-amber-400",
  "off-track": "text-red-700 dark:text-red-400",
  "no-goal": "text-muted-foreground",
};

const OVERALL_LABEL: Record<GoalStatus, string> = {
  "on-track": "Healthy",
  "at-risk": "Watch closely",
  "off-track": "Needs attention",
  "no-goal": "No goals set yet",
};

const SEVERITY: Record<GoalStatus, number> = { "off-track": 0, "at-risk": 1, "on-track": 2, "no-goal": 3 };

function StatusDot({ status, className }: { status: GoalStatus; className?: string }) {
  return <span aria-hidden className={cn("inline-block size-2 shrink-0 rounded-full", STATUS_COLOR[status], className)} />;
}

export function HealthScorecard({ data }: { data: HealthScorecardData }) {
  const groups: HealthGroup[] = data.groups?.length
    ? data.groups
    : [{ id: "finance", label: "", href: "/home", metrics: data.metrics }];

  return (
    <div className="flex flex-col gap-4">
      <SummaryCard data={data} groups={groups} />
      <div className="grid gap-4 lg:grid-cols-2">
        {groups.map((g) => (
          <AreaCard key={g.id} group={g} preset={data.win.preset} />
        ))}
      </div>
    </div>
  );
}

function SummaryCard({ data, groups }: { data: HealthScorecardData; groups: HealthGroup[] }) {
  const overall = data.overallStatus ?? "no-goal";
  const scored = groups.flatMap((g) =>
    g.metrics.filter((m) => m.status !== "no-goal").map((m) => ({ metric: m, group: g })),
  );
  const count = (s: GoalStatus) => scored.filter((x) => x.metric.status === s).length;
  const attention = scored
    .filter((x) => x.metric.status === "off-track" || x.metric.status === "at-risk")
    .sort((a, b) => SEVERITY[a.metric.status] - SEVERITY[b.metric.status] || (a.metric.pace ?? 0) - (b.metric.pace ?? 0));

  const summary = !scored.length
    ? "Set goals to see how the store is tracking."
    : attention.length
      ? `${attention.length} of ${scored.length} goals need attention`
      : `All ${scored.length} goals on track`;

  return (
    <Card data-testid="health-summary">
      <CardHeader className="gap-3">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Store health · {data.windowLabel}
        </p>
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <div className="flex flex-col gap-1">
            <h2 className={cn("flex items-center gap-2 text-2xl font-semibold tracking-tight", STATUS_TEXT[overall])}>
              <StatusDot status={overall} className="size-2.5" />
              {OVERALL_LABEL[overall]}
            </h2>
            <p className="text-sm text-muted-foreground">{summary}</p>
          </div>
          {scored.length ? (
            <ul className="flex flex-wrap gap-1.5" aria-label="Goals by status">
              {(["off-track", "at-risk", "on-track"] as const).map((s) => (
                <li
                  key={s}
                  className={cn(
                    "flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs whitespace-nowrap tabular-nums",
                    count(s) ? "text-foreground" : "text-muted-foreground/60",
                  )}
                >
                  <StatusDot status={count(s) ? s : "no-goal"} />
                  <span className="font-medium">{count(s)}</span>
                  <span className="text-muted-foreground">{STATUS_LABEL[s].toLowerCase()}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </CardHeader>
      {attention.length ? (
        <CardContent>
          <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
            {attention.map(({ metric: m, group: g }) => (
              <li key={m.key}>
                <Link
                  href={periodHref(g.href, data.win.preset)}
                  className="flex min-h-12 items-center gap-3 px-3 py-2 transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
                >
                  <StatusDot status={m.status} />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="text-sm font-medium text-foreground">{m.label}</span>
                    {m.deltaFormatted ? (
                      <span className={cn("text-xs", STATUS_TEXT[m.status])}>{m.deltaFormatted}</span>
                    ) : null}
                  </span>
                  <span className="shrink-0 text-sm font-semibold whitespace-nowrap tabular-nums">{m.formatted}</span>
                  <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        </CardContent>
      ) : null}
    </Card>
  );
}

function AreaCard({ group: g, preset }: { group: HealthGroup; preset: RangePreset }) {
  const scored = g.metrics.filter((m) => m.status !== "no-goal");
  const rollup = scored.reduce<GoalStatus>(
    (worst, m) => (SEVERITY[m.status] < SEVERITY[worst] ? m.status : worst),
    scored.length ? "on-track" : "no-goal",
  );

  return (
    <Card size="sm" data-testid={`health-area-${g.id}`}>
      {g.label ? (
        <CardHeader className="border-b pb-3">
          <Link
            href={periodHref(g.href, preset)}
            className="group/area -mx-1 flex min-h-9 items-center gap-2 rounded-md px-1 text-sm font-semibold text-foreground"
          >
            <StatusDot status={rollup} />
            <span className="flex-1">{g.label}</span>
            <span className="flex items-center gap-0.5 text-xs font-normal text-muted-foreground transition-colors group-hover/area:text-foreground">
              Open
              <ChevronRightIcon className="size-3.5" aria-hidden />
            </span>
          </Link>
        </CardHeader>
      ) : null}
      <CardContent className="flex flex-col divide-y divide-border">
        {g.metrics.map((m) => (
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
  const { isPending, error, run, setError } = useConsoleAction();

  const field = m.goalKey ? GOAL_FIELDS.find((f) => f.key === m.goalKey) : undefined;
  const editable = Boolean(field && m.goalKey);
  const tracked = m.status !== "no-goal";

  function startEdit() {
    if (!field) return;
    const raw = m.rawGoal ?? "";
    setInputValue(field.kind === "percent" ? fractionToPercentInput(raw) : raw);
    setError(null);
    setEditing(true);
  }

  function save() {
    if (!field || !m.goalKey) return;
    let stored = inputValue;
    if (field.kind === "percent") stored = percentInputToFraction(inputValue);
    else if (field.kind === "hours") {
      const parsed = parseHoursGoalInput(inputValue);
      if (parsed == null) {
        setError("Enter a weekly hours goal greater than 0.");
        return;
      }
      stored = parsed;
    }
    void run(() => saveGoalAction(m.goalKey!, stored), { saving: "Saving…" }).then((ack) => {
      if (ack.ok) {
        setEditing(false);
        router.refresh();
      }
    });
  }

  const unit =
    field?.kind === "percent" ? "%" : field?.kind === "minutes" ? "min" : field?.kind === "hours" ? "hrs" : null;

  return (
    <div className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0" data-testid={`metric-${m.key}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col">
          <span className="flex items-center gap-1 text-sm text-foreground">
            <span>{m.label}</span>
            <Tooltip>
              <TooltipTrigger
                render={
                  <button
                    type="button"
                    aria-label={`About ${m.label}`}
                    className="-m-1.5 shrink-0 p-1.5 text-muted-foreground/60 hover:text-foreground"
                  >
                    <InfoIcon className="size-3.5" />
                  </button>
                }
              />
              <TooltipContent>{m.info}</TooltipContent>
            </Tooltip>
          </span>
          {m.detail ? <span className="text-xs text-muted-foreground">{m.detail}</span> : null}
        </div>
        <span className="shrink-0 text-base font-semibold whitespace-nowrap tabular-nums">{m.formatted}</span>
      </div>

      {tracked ? (
        <GoalBar
          status={m.status}
          pace={m.pace}
          actual={m.bar ? m.bar.actual : m.actual}
          goal={m.bar ? m.bar.goal : m.goal}
          goalLabel={m.goalFormatted}
        />
      ) : null}

      {editing && field ? (
        <div className="flex items-center gap-1">
          <div className="relative w-28">
            <Input
              autoFocus
              type="text"
              inputMode="decimal"
              aria-label={`${m.label} goal`}
              value={inputValue}
              onChange={(e) =>
                setInputValue(field.kind === "dollars" ? sanitizeDollarInput(e.target.value) : e.target.value)
              }
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") setEditing(false);
              }}
              className={cn("h-8 text-sm", field.kind === "dollars" && "pl-5", unit && "pr-9")}
            />
            {field.kind === "dollars" ? (
              <span className="pointer-events-none absolute inset-y-0 left-2 flex items-center text-sm text-muted-foreground">$</span>
            ) : null}
            {unit ? (
              <span className="pointer-events-none absolute inset-y-0 right-2 flex items-center text-xs text-muted-foreground">{unit}</span>
            ) : null}
          </div>
          <Button size="icon-sm" variant="ghost" disabled={isPending} onClick={save} aria-label="Save goal">
            <CheckIcon className="size-4" />
          </Button>
          <Button size="icon-sm" variant="ghost" disabled={isPending} onClick={() => setEditing(false)} aria-label="Cancel">
            <XIcon className="size-4" />
          </Button>
        </div>
      ) : tracked || editable ? (
        <div className="flex items-center gap-2 text-xs">
          {tracked ? (
            <span className={cn("flex min-w-0 items-center gap-1.5", STATUS_TEXT[m.status])}>
              <span className="font-medium whitespace-nowrap">{STATUS_LABEL[m.status]}</span>
              {m.deltaFormatted ? <span className="text-muted-foreground">· {m.deltaFormatted}</span> : null}
            </span>
          ) : null}
          {editable ? (
            <button
              type="button"
              aria-label={`${tracked ? "Edit" : "Set"} ${m.label} goal`}
              onClick={startEdit}
              className="-my-1 ml-auto flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <PencilIcon className="size-3" aria-hidden />
              {tracked ? null : <span>Set goal</span>}
            </button>
          ) : null}
        </div>
      ) : null}
      {error ? <p className="text-xs text-destructive">Save failed: {error}</p> : null}
    </div>
  );
}
