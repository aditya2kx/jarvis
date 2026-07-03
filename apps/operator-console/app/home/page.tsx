import { loadHealthScorecard } from "@/lib/kpi/health";
import { pipelineRuns } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { formatDate } from "@/lib/format";
import { HealthScorecard } from "@/components/kpi/HealthScorecard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export const revalidate = 600;

export default async function HomePage() {
  let weekly, monthly;
  let latestRunStatus: string | undefined;
  let latestRunDate: string | undefined;
  let error: string | undefined;
  try {
    [weekly, monthly] = await Promise.all([
      loadHealthScorecard("weekly"),
      loadHealthScorecard("monthly"),
    ]);
    const runs = await pipelineRuns();
    latestRunStatus = runs[0]?.status;
    latestRunDate = runs[0]?.run_date;
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Home</h1>
        <span className="text-sm text-muted-foreground">{DEFAULT_STORE}</span>
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
                Goals editor, training quick-add, and recognition bonuses land in M4 — this queue
                will surface open restock dates, unset goals, and failed runs once those write
                paths exist.
              </p>
            </CardContent>
          </Card>

          <HealthScorecard weekly={weekly} monthly={monthly} />
        </>
      )}
    </div>
  );
}
