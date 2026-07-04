import Link from "next/link";
import { loadHealthScorecard } from "@/lib/kpi/health";
import { loadActionItems, type ActionItem } from "@/lib/kpi/actions";
import { pipelineRuns, storeConfig, payrollPeriod } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { formatDate } from "@/lib/format";
import { HealthScorecard } from "@/components/kpi/HealthScorecard";
import { GoalsDrawer } from "@/components/drawers/GoalsDrawer";
import { TrainingQuickAdd } from "@/components/drawers/TrainingQuickAdd";
import { RecognitionDrawer } from "@/components/drawers/RecognitionDrawer";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { FEATURES } from "@/lib/config/features";
import type { GoalKey } from "@/lib/bq/writes";

export const revalidate = 600;

export default async function HomePage() {
  let weekly, monthly;
  let latestRunStatus: string | undefined;
  let latestRunDate: string | undefined;
  let goals: Partial<Record<GoalKey, string>> = {};
  let actionItems: ActionItem[] = [];
  let defaultPayPeriod = "";
  let error: string | undefined;
  try {
    [weekly, monthly] = await Promise.all([
      loadHealthScorecard("weekly"),
      loadHealthScorecard("monthly"),
    ]);
    const [runs, config, periods, actions] = await Promise.all([
      pipelineRuns(),
      storeConfig(DEFAULT_STORE),
      payrollPeriod(1),
      loadActionItems(),
    ]);
    latestRunStatus = runs[0]?.status;
    latestRunDate = runs[0]?.run_date;
    goals = Object.fromEntries(
      config.filter((r) => r.key.startsWith("goal_")).map((r) => [r.key as GoalKey, r.value]),
    );
    defaultPayPeriod = periods[0]?.period_start ?? "";
    actionItems = actions;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold tracking-tight">Home</h1>
          <div className="flex items-center gap-2">
            {FEATURES.writeGoals ? <GoalsDrawer current={goals} /> : null}
            <span className="text-sm text-muted-foreground">{DEFAULT_STORE}</span>
          </div>
        </div>
        <p className="text-sm text-muted-foreground">
          Your store at a glance · {DEFAULT_STORE}
        </p>
      </div>

      {error || !weekly || !monthly ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable{error ? `: ${error}` : ""} — this is expected locally without ADC/BQ
          access; deployed behind IAP this reads live.
        </p>
      ) : (
        <>
          <HealthScorecard weekly={weekly} monthly={monthly} />

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
                  Nothing needs attention right now. Use &quot;Edit goals&quot; above to set
                  weekly/monthly targets — the scorecard below tracks against them as soon as
                  they&apos;re set.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Quick actions
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap items-center gap-2">
              {FEATURES.writeTraining ? <TrainingQuickAdd /> : null}
              {FEATURES.writeRecognition ? (
                <RecognitionDrawer defaultPayPeriod={defaultPayPeriod} />
              ) : null}
              {FEATURES.writeRestock ? (
                <Link href="/inventory" className={buttonVariants({ variant: "outline", size: "sm" })}>
                  + Planned restock…
                </Link>
              ) : null}
              <span className="text-xs text-muted-foreground">
                Write-backs land in BQ via the same MERGE paths as the Slack commands.
              </span>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
