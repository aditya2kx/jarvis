import { InfoIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { LaborForwardSummary as Summary } from "@/lib/kpi/labor-forward";

function fmtPct(n: number | null): string {
  return n == null || !Number.isFinite(n) ? "—" : `${(n * 100).toFixed(1)}%`;
}

function fmtDollars(n: number | null): string {
  return n == null || !Number.isFinite(n)
    ? "—"
    : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function Cell({
  label,
  pct,
  dollars,
  allInPct,
  allInDollars,
  showAllIn,
}: {
  label: string;
  pct: number | null;
  dollars: number | null;
  allInPct: number | null;
  allInDollars: number | null;
  showAllIn: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-border/60 bg-muted/30 px-3 py-2.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className="text-lg font-semibold tabular-nums tracking-tight">{fmtPct(pct)}</span>
      <span className="text-xs tabular-nums text-muted-foreground">{fmtDollars(dollars)} wage</span>
      {showAllIn ? (
        <span className="text-xs tabular-nums text-muted-foreground">
          {fmtPct(allInPct)} / {fmtDollars(allInDollars)} all-in
        </span>
      ) : null}
    </div>
  );
}

/** 2×2 completed vs projected labor summary for the Labor page (Issue #166). */
export function LaborForwardSummaryCard({ data }: { data: Summary }) {
  const showAllIn = data.laborBurdenPct > 0;
  const captionParts = [
    data.avgPtWage != null ? `avg PT wage $${data.avgPtWage.toFixed(2)}` : null,
    data.fwdDays > 0 ? `${data.fwdDays} scheduled day${data.fwdDays === 1 ? "" : "s"} (${data.fwdScheduledHours.toFixed(1)} hrs)` : "no upcoming schedule in period",
    data.aov != null ? `AOV $${data.aov.toFixed(2)}` : null,
    showAllIn ? `burden ${(data.laborBurdenPct * 100).toFixed(0)}%` : null,
  ].filter(Boolean);

  return (
    <Card>
      <CardHeader className="gap-1">
        <div className="flex items-center gap-1.5">
          <CardTitle className="text-sm font-medium text-muted-foreground">
            Labor % of net sales — completed vs projected
          </CardTitle>
          <Tooltip>
            <TooltipTrigger
              render={
                <button
                  type="button"
                  aria-label="About labor cost projection"
                  className="text-muted-foreground/70 hover:text-foreground"
                >
                  <InfoIcon className="size-3.5" />
                </button>
              }
            />
            <TooltipContent className="max-w-xs">
              Percentages are labor $ ÷ net sales. Completed = punches × wage rates through
              yesterday (America/Chicago). Projected adds only remaining Period days that have
              ADP scheduled hours (× avg PT wage) plus trailing FT $/open-day, over completed
              sales + forecast orders × AOV for those same scheduled days — no double-count of
              today, no forecast-only days. All-in multiplies wage cost by (1 + labor_burden_pct)
              when that store_config key is set.
            </TooltipContent>
          </Tooltip>
        </div>
        <CardDescription className="text-xs">{captionParts.join(" · ")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Part-time</p>
          <div className="grid grid-cols-2 gap-2">
            <Cell
              label="Completed"
              pct={data.hasCompleted ? data.completedPtPct : null}
              dollars={data.hasCompleted ? data.completedPtCost : null}
              allInPct={data.completedPtPctAllIn}
              allInDollars={data.completedPtCostAllIn}
              showAllIn={showAllIn}
            />
            <Cell
              label="Projected (incl. scheduled)"
              pct={data.hasForward || data.hasCompleted ? data.projectedPtPct : null}
              dollars={data.hasForward || data.hasCompleted ? data.projectedPtCost : null}
              allInPct={data.projectedPtPctAllIn}
              allInDollars={data.projectedPtCostAllIn}
              showAllIn={showAllIn}
            />
          </div>
        </div>
        <div className="grid gap-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Total</p>
          <div className="grid grid-cols-2 gap-2">
            <Cell
              label="Completed"
              pct={data.hasCompleted ? data.completedTotalPct : null}
              dollars={data.hasCompleted ? data.completedTotalCost : null}
              allInPct={data.completedTotalPctAllIn}
              allInDollars={data.completedTotalCostAllIn}
              showAllIn={showAllIn}
            />
            <Cell
              label="Projected (incl. scheduled)"
              pct={data.hasForward || data.hasCompleted ? data.projectedTotalPct : null}
              dollars={data.hasForward || data.hasCompleted ? data.projectedTotalCost : null}
              allInPct={data.projectedTotalPctAllIn}
              allInDollars={data.projectedTotalCostAllIn}
              showAllIn={showAllIn}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
