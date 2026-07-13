import Link from "next/link";
import { loadHealthScorecard, type HealthScorecard as HealthScorecardData } from "@/lib/kpi/health";
import { loadActionItems, type ActionItem } from "@/lib/kpi/actions";
import { pipelineRuns, storeConfig, payrollPeriod } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { formatDate } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { HealthScorecard } from "@/components/kpi/HealthScorecard";
import { PageHeader } from "@/components/shell/PageHeader";
import { GoalsDrawer } from "@/components/drawers/GoalsDrawer";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { RANGE_PRESETS } from "@/lib/filters/range";
import { resolvePageRange } from "@/lib/filters/period";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { FEATURES } from "@/lib/config/features";
import type { GoalKey } from "@/lib/bq/writes";

export const dynamic = "force-dynamic";

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string }>;
}) {
  // Cookie + URL keep Period in lockstep with Sales/Labor/… (default this_month).
  const win = await resolvePageRange((await searchParams).range);

  let health: HealthScorecardData | undefined;
  let latestRunStatus: string | undefined;
  let latestRunDate: string | undefined;
  let goals: Partial<Record<GoalKey, string>> = {};
  let actionItems: ActionItem[] = [];
  let error: string | undefined;
  try {
    health = await loadHealthScorecard(win);
    const [runs, config, actions] = await Promise.all([
      pipelineRuns(),
      storeConfig(DEFAULT_STORE),
      loadActionItems(),
    ]);
    latestRunStatus = runs[0]?.status;
    latestRunDate = runs[0]?.run_date;
    goals = Object.fromEntries(
      config.filter((r) => r.key.startsWith("goal_")).map((r) => [r.key as GoalKey, r.value]),
    );
    actionItems = actions;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const showActionCard =
    actionItems.length > 0 || (latestRunStatus != null && latestRunStatus !== "success");

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Home"
        subtitle={`Your store at a glance · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <div className="flex flex-wrap items-center gap-2">
            <FilterSelect label="Period" param="range" value={win.preset} options={RANGE_PRESETS} basePath="/home" />
            {FEATURES.writeGoals ? <GoalsDrawer current={goals} /> : null}
          </div>
        }
      />

      {error || !health ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable{error ? `: ${error}` : ""} — this is expected locally without ADC/BQ
          access; deployed behind IAP this reads live.
        </p>
      ) : (
        <>
          <HealthScorecard data={health} />

          {showActionCard ? (
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Needs your action
                </CardTitle>
                {latestRunStatus && latestRunStatus !== "success" ? (
                  <Badge variant="destructive">
                    Last pipeline run {latestRunStatus} ({latestRunDate ? formatDate(latestRunDate) : "unknown"})
                  </Badge>
                ) : (
                  <Badge variant="default">Pipeline healthy</Badge>
                )}
              </CardHeader>
              <CardContent>
                {actionItems.length ? (
                  <ul className="flex flex-col divide-y divide-border">
                    {actionItems.map((item) => (
                      <li
                        key={item.key}
                        className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0"
                      >
                        <span className="text-sm">{item.text}</span>
                        <Link href={item.href} className={buttonVariants({ variant: "outline", size: "sm" })}>
                          {item.linkLabel} →
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Pipeline needs attention — check Pipeline Health for details.
                  </p>
                )}
              </CardContent>
            </Card>
          ) : null}
        </>
      )}
    </div>
  );
}
