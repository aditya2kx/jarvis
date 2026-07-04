import "server-only";
import {
  orderRecoCombined,
  payrollPeriod,
  sourcePulls,
  pipelineRuns,
} from "@/lib/bq/queries";
import { formatDate } from "@/lib/format";

export interface ActionItem {
  key: string;
  text: string;
  href: string;
  linkLabel: string;
  severity: "warn" | "info";
}

const LOW_STOCK_DAYS = 4;
const WAGE_DIFF_REVIEW_CENTS = 500; // $5 — same "needs a human look" bar as the Payroll screen's non-zero diff rows

// Real, derived from the same views the individual screens already read —
// no new metric math, just a cross-screen "what needs a decision" rollup
// (Figma "Needs your action" card). Caps at 4 items to match the design's
// row count; if more are true, the 5th+ are simply omitted rather than
// growing the card unboundedly.
export async function loadActionItems(): Promise<ActionItem[]> {
  const items: ActionItem[] = [];

  const [reco, periods, pulls, runs] = await Promise.all([
    orderRecoCombined().catch(() => []),
    payrollPeriod(1).catch(() => []),
    sourcePulls().catch(() => []),
    pipelineRuns().catch(() => []),
  ]);

  for (const r of reco) {
    const days = r["Days Left 1"];
    if (days != null && days < LOW_STOCK_DAYS) {
      items.push({
        key: `inv-${r.Item}`,
        text: `Order ${r["Order Tubs 1"] ?? "?"} tubs ${r.Item} — ${days.toFixed(1)} days to empty`,
        href: "/inventory",
        linkLabel: "Ordering",
        severity: "warn",
      });
    }
  }

  const openPeriod = periods.find((p) => p.is_open);
  if (openPeriod) {
    const diffCents = periods
      .filter((p) => p.period_start === openPeriod.period_start)
      .reduce((s, p) => s + Math.abs(Math.round((p.wage_diff ?? 0) * 100)), 0);
    if (diffCents > WAGE_DIFF_REVIEW_CENTS) {
      items.push({
        key: "payroll-diff",
        text: `Pay period ${formatDate(openPeriod.period_end)} — wage diffs to review`,
        href: "/payroll",
        linkLabel: "Payroll",
        severity: "warn",
      });
    }
  }

  const latestRun = runs[0];
  if (latestRun && latestRun.status !== "success") {
    items.push({
      key: "pipeline-run",
      text: `Last nightly run ${latestRun.status} (${formatDate(latestRun.run_date)})`,
      href: "/pipeline",
      linkLabel: "Pipeline Health",
      severity: "warn",
    });
  }
  for (const p of pulls) {
    if (p.status !== "success" && p.run_date === latestRun?.run_date) {
      items.push({
        key: `pull-${p.source}`,
        text: `Source "${p.source}" failed on the last run`,
        href: "/pipeline",
        linkLabel: "Pipeline Health",
        severity: "warn",
      });
    }
  }

  return items.slice(0, 4);
}
