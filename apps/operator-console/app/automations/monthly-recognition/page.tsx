import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { listPayPeriodsWithPaidStatus, type PayPeriodOption } from "@/lib/bq/queries";
import { formatDate } from "@/lib/format";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { DEFAULT_WORKSPACE_ID } from "@/lib/automations/clickup";
import { FEATURES } from "@/lib/config/features";
import { RECOGNITION } from "@/lib/config/stores";
import { senderEmail } from "@/lib/recognition/gmail";
import { awardMonthForPeriod, isAwardMonth, monthLabel, recentAwardMonths } from "@/lib/recognition/month";
import { giftCardScopeProblem } from "@/lib/recognition/square";
import { members as loadMembers } from "@/lib/recognition/store";
import { loadRecognitionView, type RecognitionView } from "@/lib/recognition/view";
import type { Member } from "@/lib/recognition/parse";
import { RecognitionEditor } from "./RecognitionEditor";

export const dynamic = "force-dynamic";

const BASE = "/automations/monthly-recognition";

function pickPeriod(value: string | undefined, options: PayPeriodOption[]): PayPeriodOption | null {
  return (
    options.find((o) => o.period_start === value) ??
    options.find((o) => o.is_current) ??
    options[0] ??
    null
  );
}

export default async function MonthlyRecognitionPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string; month?: string }>;
}) {
  const sp = await searchParams;
  let error: string | undefined;
  let periods: PayPeriodOption[] = [];
  let view: RecognitionView | null = null;
  let memberList: Member[] = [];

  try {
    periods = await listPayPeriodsWithPaidStatus(6);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }
  const period = pickPeriod(sp.period, periods);
  const defaultMonth = period ? awardMonthForPeriod(period.period_end) : null;
  const awardMonth = isAwardMonth(sp.month) ? sp.month : defaultMonth;

  const [squareProblem, sender] = await Promise.all([
    FEATURES.recognitionGiftCards ? giftCardScopeProblem() : Promise.resolve(null),
    senderEmail(),
  ]);

  if (!error && awardMonth) {
    try {
      [view, memberList] = await Promise.all([
        loadRecognitionView(DEFAULT_STORE, awardMonth),
        loadMembers(DEFAULT_WORKSPACE_ID),
      ]);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  const periodOptions = periods.map((o) => ({
    value: o.period_start,
    label: `${formatDate(o.period_start)} – ${formatDate(o.period_end)}${o.is_current ? " · Current" : ""}`,
  }));
  const monthOptions = (defaultMonth ? recentAwardMonths(defaultMonth, 6) : []).map((m) => ({
    value: m,
    label: m === defaultMonth ? `${monthLabel(m)} · this cycle` : monthLabel(m),
  }));

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Monthly recognition"
        subtitle="MVP + High Five from #monthly-recognition → gift cards, email, and the team post"
        right={
          <div className="flex flex-wrap gap-2">
            {periodOptions.length ? (
              <FilterSelect
                label="Payroll cycle"
                param="period"
                value={period?.period_start ?? periodOptions[0].value}
                options={periodOptions}
                basePath={BASE}
              />
            ) : null}
            {monthOptions.length && awardMonth ? (
              <FilterSelect
                label="Award month"
                param="month"
                value={awardMonth}
                options={monthOptions}
                basePath={BASE}
                extraParams={period ? { period: period.period_start } : {}}
              />
            ) : null}
          </div>
        }
      />
      {error || !view ? (
        <p className="text-sm text-muted-foreground">
          Data unavailable: {error ?? "no payroll cycles"}. If tables are missing, apply migration 087 (
          <code className="rounded bg-muted px-1">ensure_schema</code>).
        </p>
      ) : (
        <RecognitionEditor
          key={view.awardMonth}
          view={view}
          members={memberList}
          giftCardsEnabled={FEATURES.recognitionGiftCards}
          squareProblem={squareProblem}
          senderEmail={sender}
          leadClickupUserId={RECOGNITION[DEFAULT_STORE]?.leadClickupUserId ?? null}
        />
      )}
    </div>
  );
}
