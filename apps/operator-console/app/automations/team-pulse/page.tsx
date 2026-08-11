import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import {
  getAutomation,
  listAutomationPosts,
  listPayPeriodsWithPaidStatus,
  reviewBonusMetaForPeriod,
  type PayPeriodOption,
} from "@/lib/bq/queries";
import { formatDate } from "@/lib/format";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { AUTOMATION_ID } from "@/lib/automations/teamPulse";
import {
  listNamedChannels,
  listWorkspaceMembers,
  type ClickUpNamedOption,
} from "@/lib/automations/clickup";
import { TeamPulseEditor } from "./TeamPulseEditor";

export const dynamic = "force-dynamic";

function parsePeriodStart(
  value: string | string[] | undefined,
  options: PayPeriodOption[],
): string | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw && options.some((o) => o.period_start === raw)) return raw;
  const current = options.find((o) => o.is_current && o.unpaid);
  if (current) return current.period_start;
  const unpaid = options.find((o) => o.unpaid);
  return unpaid?.period_start ?? options[0]?.period_start ?? null;
}

export default async function TeamPulsePage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string }>;
}) {
  const sp = await searchParams;
  let error: string | undefined;
  let cfg = null;
  let posts: Awaited<ReturnType<typeof listAutomationPosts>> = [];
  let channels: ClickUpNamedOption[] = [];
  let members: ClickUpNamedOption[] = [];
  let clickupError: string | undefined;
  let periodOptions: PayPeriodOption[] = [];
  let reviewMeta: Awaited<ReturnType<typeof reviewBonusMetaForPeriod>> = null;

  try {
    const settled = await Promise.all([
      getAutomation(DEFAULT_STORE, AUTOMATION_ID),
      listAutomationPosts(DEFAULT_STORE, AUTOMATION_ID),
      listPayPeriodsWithPaidStatus(6),
    ]);
    cfg = settled[0];
    posts = settled[1];
    periodOptions = settled[2];
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const selectedPeriodStart = parsePeriodStart(sp.period, periodOptions);
  const selectedOpt = periodOptions.find(
    (o) => o.period_start === selectedPeriodStart,
  );
  const periodEnd = selectedOpt?.period_end ?? null;

  if (!error && selectedPeriodStart) {
    try {
      reviewMeta = await reviewBonusMetaForPeriod(selectedPeriodStart);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  try {
    [channels, members] = await Promise.all([
      listNamedChannels(),
      listWorkspaceMembers(),
    ]);
  } catch (e) {
    clickupError = e instanceof Error ? e.message : String(e);
  }

  const periodSelectOptions = periodOptions.map((o) => ({
    value: o.period_start,
    label: `${formatDate(o.period_start)} – ${formatDate(o.period_end)} · ${
      o.is_current ? "Current · " : ""
    }${o.unpaid ? "Unpaid" : "Paid (ADP)"}`,
  }));

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Team pulse"
        subtitle="ClickUp motivating leaderboard — schedule, template, and history"
        right={
          periodSelectOptions.length ? (
            <FilterSelect
              label="Period"
              param="period"
              value={selectedPeriodStart ?? periodSelectOptions[0].value}
              options={periodSelectOptions}
              basePath="/automations/team-pulse"
            />
          ) : null
        }
      />
      {error ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable: {error}. If tables are missing, apply migration 054
          (<code className="rounded bg-muted px-1">ensure_schema</code>).
        </p>
      ) : (
        <TeamPulseEditor
          initial={cfg}
          posts={posts}
          channels={channels}
          members={members}
          clickupError={clickupError}
          reviewMeta={reviewMeta}
          selectedPeriodStart={selectedPeriodStart ?? ""}
          periodEnd={periodEnd}
        />
      )}
    </div>
  );
}
