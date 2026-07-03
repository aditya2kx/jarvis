"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { GoalBar } from "./GoalBar";
import type { HealthScorecard as HealthScorecardData } from "@/lib/kpi/health";

const STATUS_LABEL: Record<string, string> = {
  "on-track": "On track",
  "at-risk": "At risk",
  "off-track": "Off track",
  "no-goal": "No goal set",
};

// Client component only to hold the weekly/monthly toggle state — both
// windows are pre-fetched server-side (app/home/page.tsx), no client fetch.
export function HealthScorecard({
  weekly,
  monthly,
}: {
  weekly: HealthScorecardData;
  monthly: HealthScorecardData;
}) {
  const [window, setWindow] = useState<"weekly" | "monthly">("weekly");
  const data = window === "weekly" ? weekly : monthly;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          Operational health — {data.windowLabel}
        </CardTitle>
        <div className="flex gap-1 rounded-md bg-secondary p-0.5 text-xs">
          {(["weekly", "monthly"] as const).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={
                "rounded-sm px-2 py-1 capitalize transition-colors " +
                (window === w
                  ? "bg-background font-medium text-foreground shadow-sm"
                  : "text-muted-foreground")
              }
            >
              {w}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        {data.metrics.map((m) => (
          <div key={m.key} className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-muted-foreground">{m.label}</span>
              <Badge variant={m.status === "on-track" ? "default" : m.status === "no-goal" ? "secondary" : "destructive"}>
                {STATUS_LABEL[m.status]}
              </Badge>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold">{m.formatted}</span>
              <span className="text-xs text-muted-foreground">goal {m.goalFormatted}</span>
            </div>
            <GoalBar status={m.status} pace={m.pace} />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
