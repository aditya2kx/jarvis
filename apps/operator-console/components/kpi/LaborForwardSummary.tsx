import { InfoIcon } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { LaborForwardSummary as Summary } from "@/lib/kpi/labor-forward";
import { viewForLaborLens, type LaborLens } from "@/lib/kpi/labor-lens";

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
  unit,
  coverage,
}: {
  label: string;
  pct: number | null;
  dollars: number | null;
  unit: string;
  coverage: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-border/60 bg-muted/30 px-3 py-2.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className="text-lg font-semibold tabular-nums tracking-tight">{fmtPct(pct)}</span>
      <span className="text-xs tabular-nums text-muted-foreground">
        {fmtDollars(dollars)} {unit}
      </span>
      <span className="text-[11px] leading-snug text-muted-foreground/90">{coverage}</span>
    </div>
  );
}

/** Labor % summary for one lens (Wage / Paid / Blended) — Issue #166. */
export function LaborForwardSummaryCard({
  data,
  lens,
  periodDays = 0,
}: {
  data: Summary;
  lens: LaborLens;
  /** Inclusive calendar days in the selected Period (for coverage %). */
  periodDays?: number;
}) {
  const view = viewForLaborLens(data, lens, periodDays);
  const unit = lens === "paid" ? "paid" : "wage";

  return (
    <Card>
      <CardHeader className="gap-1">
        <div className="flex items-center gap-1.5">
          <CardTitle className="text-sm font-medium text-muted-foreground">{view.title}</CardTitle>
          <Tooltip>
            <TooltipTrigger
              render={
                <button
                  type="button"
                  aria-label="About this labor lens"
                  className="text-muted-foreground/70 hover:text-foreground"
                >
                  <InfoIcon className="size-3.5" />
                </button>
              }
            />
            <TooltipContent className="max-w-xs">
              <p className="mb-1.5 font-medium">Three lenses (pick one above)</p>
              <ul className="list-disc space-y-1 pl-4 text-xs">
                <li>
                  <strong>Wage</strong> — completed days, hourly wages only (PT and PT+FT).
                </li>
                <li>
                  <strong>Paid payroll</strong> — completed days, wage + ER burden; not full ADP
                  paycheck lines.
                </li>
                <li>
                  <strong>Blended (schedule)</strong> — completed wage + remaining scheduled days
                  at wage (no burden; unscheduled days omitted from both cost and sales).
                </li>
              </ul>
              <p className="mt-2 text-xs text-muted-foreground">
                Dollar amounts always show how many days (and % of Period) they cover.
              </p>
            </TooltipContent>
          </Tooltip>
        </div>
        <CardDescription className="text-xs">{view.description}</CardDescription>
      </CardHeader>
      <CardContent>
        {view.paidUnavailable ? (
          <p className="text-sm text-muted-foreground">{view.description}</p>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <Cell
              label={view.ptLabel}
              pct={view.ptPct}
              dollars={view.ptDollars}
              unit={unit}
              coverage={view.coverage}
            />
            <Cell
              label={view.totalLabel}
              pct={view.totalPct}
              dollars={view.totalDollars}
              unit={unit}
              coverage={view.coverage}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
