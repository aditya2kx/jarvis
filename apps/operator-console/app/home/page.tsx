import { loadHealthScorecard } from "@/lib/kpi/health";
import { pipelineRuns, storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { formatDate } from "@/lib/format";
import { HealthScorecard } from "@/components/kpi/HealthScorecard";
import { GoalsDrawer } from "@/components/drawers/GoalsDrawer";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { FEATURES } from "@/lib/config/features";
import type { GoalKey } from "@/lib/bq/writes";

export const revalidate = 600;

export default async function HomePage() {
  let weekly, monthly;
  let latestRunStatus: string | undefined;
  let latestRunDate: string | undefined;
  let goals: Partial<Record<GoalKey, string>> = {};
  let error: string | undefined;
  try {
    [weekly, monthly] = await Promise.all([
      loadHealthScorecard("weekly"),
      loadHealthScorecard("monthly"),
    ]);
    const [runs, config] = await Promise.all([pipelineRuns(), storeConfig(DEFAULT_STORE)]);
    latestRunStatus = runs[0]?.status;
    latestRunDate = runs[0]?.run_date;
    goals = Object.fromEntries(
      config.filter((r) => r.key.startsWith("goal_")).map((r) => [r.key as GoalKey, r.value]),
    );
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Home</h1>
        <div className="flex items-center gap-2">
          {FEATURES.writeGoals ? <GoalsDrawer current={goals} /> : null}
          <span className="text-sm text-muted-foreground">{DEFAULT_STORE}</span>
        </div>
      </div>

      {error || !weekly || !monthly ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable{error ? `: ${error}` : ""} — this is expected locally without ADC/BQ
          access; deployed behind IAP this reads live.
        </p>
      ) : (
        <>
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
              <p className="text-sm text-muted-foreground">
                Use &quot;Edit goals&quot; above to set weekly/monthly targets — the scorecard
                below tracks against them as soon as they&apos;re set.
              </p>
            </CardContent>
          </Card>

          <HealthScorecard weekly={weekly} monthly={monthly} />
        </>
      )}
    </div>
  );
}
