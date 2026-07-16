import { loadHealthScorecard, type HealthScorecard as HealthScorecardData } from "@/lib/kpi/health";
import { storeConfig } from "@/lib/bq/queries";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { storeDisplayName } from "@/lib/config/stores";
import { HealthScorecard } from "@/components/kpi/HealthScorecard";
import { PageHeader } from "@/components/shell/PageHeader";
import { GoalsDrawer } from "@/components/drawers/GoalsDrawer";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterPills } from "@/components/filters/FilterPills";
import { RANGE_PRESETS } from "@/lib/filters/range";
import { resolvePageRange } from "@/lib/filters/period";
import { LABOR_LENS_OPTIONS, parseLaborLens } from "@/lib/kpi/labor-lens";
import { FEATURES } from "@/lib/config/features";
import type { GoalKey } from "@/lib/bq/writes";

export const dynamic = "force-dynamic";

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; lens?: string }>;
}) {
  const sp = await searchParams;
  // Cookie + URL keep Period in lockstep with Sales/Labor/… (default this_month).
  const win = await resolvePageRange(sp.range);
  const lens = parseLaborLens(sp.lens);

  let health: HealthScorecardData | undefined;
  let goals: Partial<Record<GoalKey, string>> = {};
  let error: string | undefined;
  try {
    health = await loadHealthScorecard(win, { laborLens: lens });
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
          <div className="flex flex-wrap items-center gap-2">
            <FilterSelect
              label="Period"
              param="range"
              value={win.preset}
              options={RANGE_PRESETS}
              basePath="/home"
              extraParams={{ lens }}
            />
            {FEATURES.writeGoals ? <GoalsDrawer current={goals} /> : null}
          </div>
        }
      />

      <FilterPills
        label="Labor lens"
        param="lens"
        value={lens}
        options={LABOR_LENS_OPTIONS}
        basePath="/home"
        extraParams={{ range: win.preset }}
      />

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
