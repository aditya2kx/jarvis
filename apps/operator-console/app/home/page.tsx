import { loadHealthScorecard, type HealthScorecard as HealthScorecardData } from "@/lib/kpi/health";
import { storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { storeDisplayName } from "@/lib/config/stores";
import { HealthScorecard } from "@/components/kpi/HealthScorecard";
import { PageHeader } from "@/components/shell/PageHeader";
import { InfoNote } from "@/components/shell/InfoNote";
import { GoalsDrawer } from "@/components/drawers/GoalsDrawer";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { RANGE_PRESETS } from "@/lib/filters/range";
import { resolvePageRange } from "@/lib/filters/period";
import { FEATURES } from "@/lib/config/features";
import type { GoalKey } from "@/lib/bq/writes";

export const dynamic = "force-dynamic";

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string }>;
}) {
  const sp = await searchParams;
  const win = await resolvePageRange(sp.range);

  let health: HealthScorecardData | undefined;
  let goals: Partial<Record<GoalKey, string>> = {};
  let error: string | undefined;
  try {
    health = await loadHealthScorecard(win);
    const config = await storeConfig(DEFAULT_STORE);
    goals = Object.fromEntries(
      config.filter((r) => r.key.startsWith("goal_")).map((r) => [r.key as GoalKey, r.value]),
    );
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Home"
        subtitle={`Your store at a glance · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <FilterSelect
            label="Period"
            param="range"
            value={win.preset}
            options={RANGE_PRESETS}
            basePath="/home"
          />
        }
        actions={FEATURES.writeGoals ? <GoalsDrawer current={goals} /> : null}
      />

      <InfoNote summary="Where these numbers come from">
        Finance and Cost are bank (Plaid) numbers; Sales is Square. Labor shows ADP rate-based PT/FT/Total
        plus bank payroll (matches Cost). Categories marked exclude-from-accounting are omitted from bank
        totals. Goals cover the whole period, so early in a week or month most goals read behind.
      </InfoNote>
      {error || !health ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable{error ? `: ${error}` : ""} — this is expected locally without ADC/BQ
          access; deployed behind IAP this reads live.
        </p>
      ) : (
        <HealthScorecard data={health} />
      )}
    </div>
  );
}
