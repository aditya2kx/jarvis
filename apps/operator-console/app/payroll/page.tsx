import {
  payrollPeriod,
  reviewBonusDetail,
  recognitionBonuses,
  adpHoursScrapedAt,
  adpShiftsForPeriod,
  tipExemptions,
  listCanonicalEmployees,
  listPayPeriodsWithPaidStatus,
  payrollDraftRun,
  payrollSoloPremium,
  soloCoverageGap,
  soloPremiumDeltaDollars,
} from "@/lib/bq/queries";
import { formatCents, formatDate, formatDollars, formatHours } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterMultiSelect } from "@/components/filters/FilterMultiSelect";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TrainingQuickAdd } from "@/components/drawers/TrainingQuickAdd";
import { RecognitionDrawer } from "@/components/drawers/RecognitionDrawer";
import { PerkDrawer } from "@/components/drawers/PerkDrawer";
import { TipExemptionsEditor } from "@/components/drawers/TipExemptionsEditor";
import { FEATURES } from "@/lib/config/features";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { payPeriodKey } from "@/lib/payroll/periodKey";
import { rowMatchesLaborType } from "@/lib/payroll/laborBucket";
import { PayrollDraftButton } from "@/components/payroll/PayrollDraftButton";
import { SyncClockedHoursButton } from "@/components/labor/SyncClockedHoursButton";
import { chicagoTodayIso } from "@/lib/filters/range";
import { hasRunningBhagaJob } from "@/lib/bhaga/recompute";
import { clockedHoursTargetDate } from "@/lib/labor/actual-schedule-windows";
import { adpPayrollDetailsUrl } from "@/lib/payroll/adpLink";
import { previewLine } from "@/lib/payroll/previewDiff";
import { mergeSoloPremium } from "@/lib/payroll/solo-premium";
import {
  LABOR_TYPE_OPTIONS,
  parseLaborTypes,
  serializeLaborTypes,
} from "@/lib/filters/labor-type";
import type { ColumnDef } from "@tanstack/react-table";
import type {
  PayrollPeriodRow,
  ReviewBonusDetailRow,
  RecognitionBonusRow,
  AdpShiftRow,
  TipExemptionRow,
  PayPeriodOption,
  PayrollSoloPremiumRow,
} from "@/lib/bq/queries";
import type { PayrollRowWithSolo } from "@/lib/payroll/solo-premium";

export const dynamic = "force-dynamic";

function HeadlineStat({
  label,
  display,
  hint,
  hintWarn,
}: {
  label: string;
  display: string;
  hint?: string;
  hintWarn?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold tabular-nums">{display}</p>
        {hint ? (
          <p
            className={
              hintWarn
                ? "mt-1 text-[11px] leading-tight text-amber-700 dark:text-amber-400"
                : "mt-1 text-[11px] leading-tight text-muted-foreground"
            }
          >
            {hint}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function parsePeriodStart(
  value: string | string[] | undefined,
  options: PayPeriodOption[],
): string | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw && options.some((o) => o.period_start === raw)) return raw;
  // Default: current in-progress unpaid, else latest unpaid closed.
  const current = options.find((o) => o.is_current && o.unpaid);
  if (current) return current.period_start;
  const unpaid = options.find((o) => o.unpaid);
  return unpaid?.period_start ?? options[0]?.period_start ?? null;
}

export default async function PayrollPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string; labor_type?: string }>;
}) {
  const sp = await searchParams;
  const laborTypes = parseLaborTypes(sp.labor_type);
  const laborTypeParam = serializeLaborTypes(laborTypes);
  const laborTypeExtra: Record<string, string> = laborTypeParam
    ? { labor_type: laborTypeParam }
    : {};

  let periods: PayrollPeriodRow[] = [];
  let periodOptions: PayPeriodOption[] = [];
  let reviews: ReviewBonusDetailRow[] = [];
  let recognitions: RecognitionBonusRow[] = [];
  let shifts: AdpShiftRow[] = [];
  let exemptions: TipExemptionRow[] = [];
  let employees: string[] = [];
  let draftRun: Awaited<ReturnType<typeof payrollDraftRun>> = null;
  let soloRows: PayrollSoloPremiumRow[] = [];
  let soloDelta: number | null = null;
  let soloGap: string[] = [];
  let hoursScrapedAt: string | null = null;
  let error: string | undefined;
  try {
    const settled = await Promise.all([
      listPayPeriodsWithPaidStatus(6),
      reviewBonusDetail(30),
      recognitionBonuses(DEFAULT_STORE, 2),
    ]);
    periodOptions = settled[0];
    reviews = settled[1];
    recognitions = settled[2];
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const selectedPeriodStart = parsePeriodStart(sp.period, periodOptions);
  const selectedOpt = periodOptions.find((o) => o.period_start === selectedPeriodStart);
  const periodEnd = selectedOpt?.period_end;
  const selectedUnpaid = Boolean(selectedOpt?.unpaid);
  const selectedSubmitted = Boolean(selectedOpt?.submitted);
  const showPreviewHints = selectedUnpaid && !selectedSubmitted;

  if (!error && selectedPeriodStart && periodEnd) {
    try {
      const [periodRowsAll, run, solo, delta, gap] = await Promise.all([
        payrollPeriod(6),
        FEATURES.adpPayrollDraft
          ? payrollDraftRun(DEFAULT_STORE, selectedPeriodStart, periodEnd)
          : Promise.resolve(null),
        // A missing solo table must never blank the payroll table — base hours
        // and wages are correct without it (same contract as the draft backend).
        payrollSoloPremium(selectedPeriodStart).catch(() => []),
        soloPremiumDeltaDollars(DEFAULT_STORE).catch(() => null),
        // An unreadable coverage check counts as stale, not as clean: the point is
        // to refuse to vouch for a premium we cannot verify.
        soloCoverageGap(selectedPeriodStart, periodEnd).catch(() => ["unknown"]),
      ]);
      periods = periodRowsAll;
      draftRun = run;
      soloRows = solo;
      soloDelta = delta;
      soloGap = gap;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  const periodRows =
    selectedPeriodStart && periods.length
      ? periods.filter(
          (p) =>
            p.period_start === selectedPeriodStart &&
            rowMatchesLaborType(p.labor_type, laborTypes),
        )
      : [];
  // Solo premium (#309) is merged after the labor-type filter so the stat and
  // the table always describe the same set of people.
  const solo = mergeSoloPremium(periodRows, soloRows, soloDelta);
  const displayRows: PayrollRowWithSolo[] = solo.rows;
  const showSolo = soloRows.length > 0;

  const hasAdpEarnings = periodRows.some(
    (p) => p.adp_wages_paid != null || p.adp_total_paid != null,
  );
  const compareToAdp = hasAdpEarnings;
  const awaitingEarnings = selectedSubmitted && !hasAdpEarnings;

  const tipStart = selectedPeriodStart;
  const tipEnd = periodEnd;
  const editable = FEATURES.writeTipExemptions && selectedUnpaid;
  let refreshRunning = false;

  if (!error && tipStart && tipEnd) {
    try {
      const [s, e, empRows, hoursScraped, running] = await Promise.all([
        adpShiftsForPeriod(DEFAULT_STORE, tipStart, tipEnd),
        tipExemptions(DEFAULT_STORE, tipStart, tipEnd),
        listCanonicalEmployees(DEFAULT_STORE),
        adpHoursScrapedAt().catch(() => null),
        // Applying exemptions queues a model recompute, which the server will
        // refuse while a run is live. Ask up front so the button is disabled
        // instead of accepting a click it cannot honour.
        hasRunningBhagaJob().catch(() => false),
      ]);
      shifts = s;
      exemptions = e;
      employees = empRows.map((r) => r.employee_name);
      hoursScrapedAt = hoursScraped;
      refreshRunning = running;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  const totalHours = displayRows.reduce((s, p) => s + (p.hours_worked ?? 0), 0);
  // est_total_pay already carries the solo premium (mergeSoloPremium), so Total
  // pay stays comparable to the ADP Preview gross once rate-2 lines are keyed.
  const totalPay = displayRows.reduce((s, p) => s + (p.est_total_pay ?? 0), 0);
  const totalWages = displayRows.reduce((s, p) => s + (p.est_gross_pay ?? 0), 0);
  const totalTips = displayRows.reduce((s, p) => s + (p.tips_allocated ?? 0), 0);
  const totalBonus = displayRows.reduce((s, p) => s + (p.review_bonus ?? 0), 0);
  const totalRecognition = displayRows.reduce(
    (s, p) => s + (Number(p.recognition_bonus) || 0),
    0,
  );
  const totalPerks = displayRows.reduce((s, p) => s + (Number(p.perks) || 0), 0);
  const hoursVsPreview = previewLine(
    totalHours,
    draftRun?.preview_hours,
    "hours",
  );
  const payVsPreview = previewLine(
    totalPay,
    draftRun?.preview_gross,
    "pay",
  );

  const periodLabel =
    selectedPeriodStart && periodEnd
      ? `${formatDate(selectedPeriodStart)} – ${formatDate(periodEnd)}`
      : "—";

  const recognitionPayPeriod =
    selectedPeriodStart && periodEnd
      ? payPeriodKey(selectedPeriodStart, periodEnd)
      : "";

  const periodColumns: ColumnDef<PayrollRowWithSolo>[] = [
    { accessorKey: "employee", header: "Employee" },
    { accessorKey: "wage_rate_dollars", header: "Rate", meta: { format: { kind: "dollars" } } },
    // OT and solo are slices of Total hours, not additions to it. Labelled
    // "of which" because the bare headers read as separate buckets to add up.
    { accessorKey: "hours_worked", header: "Total hours", meta: { format: { kind: "number", digits: 2, minDigits: 2 } } },
    { accessorKey: "ot_hours", header: "of which OT", meta: { format: { kind: "number", digits: 2, minDigits: 2 } } },
    ...(showSolo
      ? [
          {
            accessorKey: "solo_hours",
            header: "of which solo",
            meta: { format: { kind: "number" as const, digits: 2, minDigits: 2 } },
          } satisfies ColumnDef<PayrollRowWithSolo>,
          {
            accessorKey: "primary_wages",
            header: "Primary wages",
            meta: { format: { kind: "dollars" as const } },
          } satisfies ColumnDef<PayrollRowWithSolo>,
          {
            accessorKey: "solo_wages",
            header: "Solo wages",
            meta: { format: { kind: "dollars" as const } },
          } satisfies ColumnDef<PayrollRowWithSolo>,
        ]
      : []),
    {
      // Blended once a solo rate is in play: the two rate lines ADP will carry,
      // added up. Without solo data this is the view's hours x base figure.
      accessorKey: showSolo ? "total_wages" : "est_gross_pay",
      header: "Est. wages",
      meta: { format: { kind: "dollars" } },
    },
    { accessorKey: "tips_allocated", header: "Tips", meta: { format: { kind: "dollars" } } },
    { accessorKey: "review_bonus", header: "Review bonus", meta: { format: { kind: "dollars" } } },
    {
      accessorKey: "recognition_bonus",
      header: "Recognition bonus",
      meta: { format: { kind: "dollars" } },
    },
    { accessorKey: "recognition_reason", header: "Bonus reason" },
    {
      accessorKey: "perks",
      header: "Perks",
      meta: { format: { kind: "perks" }, wrap: true },
    },
    { accessorKey: "est_total_pay", header: "Est. total", meta: { format: { kind: "dollars" } } },
    ...(compareToAdp
      ? [
          {
            accessorKey: "wage_diff",
            header: "Wage vs ADP",
            meta: {
              format: { kind: "adp_diff" as const, paidKey: "adp_wages_paid" },
            },
          } satisfies ColumnDef<PayrollRowWithSolo>,
          {
            accessorKey: "bonus_diff",
            header: "Bonus vs ADP",
            meta: {
              format: { kind: "adp_diff" as const, paidKey: "adp_bonus_paid" },
            },
          } satisfies ColumnDef<PayrollRowWithSolo>,
        ]
      : []),
  ];

  const reviewColumns: ColumnDef<ReviewBonusDetailRow>[] = [
    { accessorKey: "post_date_ct", header: "Posted", meta: { format: { kind: "date" } } },
    { accessorKey: "reviewer", header: "Reviewer" },
    { accessorKey: "rating", header: "Rating" },
    { accessorKey: "total_bonus", header: "Total bonus", meta: { format: { kind: "dollars" } } },
    { accessorKey: "employees_considered", header: "Employees" },
  ];

  const recognitionColumns: ColumnDef<RecognitionBonusRow>[] = [
    { accessorKey: "pay_period", header: "Pay period" },
    { accessorKey: "employee", header: "Employee" },
    { accessorKey: "amount_cents", header: "Amount", meta: { format: { kind: "cents" } } },
    { accessorKey: "reason", header: "Reason" },
  ];

  const periodSelectOptions = periodOptions.map((o) => ({
    value: o.period_start,
    label: `${formatDate(o.period_start)} – ${formatDate(o.period_end)} · ${
      o.is_current ? "Current · " : ""
    }${
      o.unpaid ? (o.submitted ? "Submitted" : "Unpaid") : "Paid (ADP)"
    }`,
  }));

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Payroll & People"
        subtitle={`Wages, tips, bonuses, and tip exemptions · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            {periodSelectOptions.length ? (
              <FilterSelect
                label="Period"
                param="period"
                value={selectedPeriodStart ?? periodSelectOptions[0].value}
                options={periodSelectOptions}
                basePath="/payroll"
                extraParams={laborTypeExtra}
              />
            ) : null}
            <FilterMultiSelect
              label="Labor type"
              param="labor_type"
              selected={laborTypes}
              options={[...LABOR_TYPE_OPTIONS]}
              basePath="/payroll"
              extraParams={{
                ...(selectedPeriodStart ? { period: selectedPeriodStart } : {}),
              }}
            />
            {FEATURES.adpPayrollDraft && selectedPeriodStart && periodEnd ? (
              <PayrollDraftButton
                periodStart={selectedPeriodStart}
                periodEnd={periodEnd}
                unpaid={selectedUnpaid}
                isCurrent={Boolean(selectedOpt?.is_current)}
                submitted={selectedSubmitted}
                historicPayrollUrl={
                  selectedUnpaid && !selectedSubmitted
                    ? null
                    : adpPayrollDetailsUrl()
                }
                initialHasPreview={draftRun?.status === "ok"}
                initialPreviewHours={draftRun?.preview_hours ?? null}
                initialPreviewGross={draftRun?.preview_gross ?? null}
                consoleHours={totalHours}
                consoleTotalPay={totalPay}
                initialStatus={
                  draftRun?.status === "running" ||
                  draftRun?.status === "ok" ||
                  draftRun?.status === "fail"
                    ? draftRun.status
                    : null
                }
              />
            ) : null}
            {periodEnd ? (
              <SyncClockedHoursButton
                lastScrapedAt={hoursScrapedAt}
                targetDate={clockedHoursTargetDate({
                  todayIso: chicagoTodayIso(),
                  periodEnd,
                })}
              />
            ) : null}
            {FEATURES.writeTraining ? <TrainingQuickAdd /> : null}
            {FEATURES.writeRecognition ? (
              <RecognitionDrawer
                defaultPayPeriod={recognitionPayPeriod}
                employees={employees}
              />
            ) : null}
            {FEATURES.writePerks ? (
              <PerkDrawer
                defaultPayPeriod={recognitionPayPeriod}
                employees={employees}
              />
            ) : null}
          </>
        }
      />

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <div className="flex flex-col gap-2">
            <p className="text-xs text-muted-foreground">
              Pay period {periodLabel}
              {selectedOpt?.is_current ? " · Current" : ""}
              {selectedUnpaid
                ? selectedSubmitted
                  ? " · Submitted (ADP)"
                  : " · Unpaid (ADP)"
                : " · Paid (ADP)"}
              {editable ? " · tip exemptions editable" : ""}
            </p>
            <div
              className={
                showSolo
                  ? "grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-9"
                  : "grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7"
              }
            >
              <HeadlineStat
                label="Total hours"
                display={`${formatHours(totalHours)}h`}
                hint={showPreviewHints ? hoursVsPreview?.label : undefined}
                hintWarn={Boolean(
                  showPreviewHints && hoursVsPreview && !hoursVsPreview.match,
                )}
              />
              <HeadlineStat
                label="Wages"
                display={formatDollars(showSolo ? solo.totalWages : totalWages)}
                hint={
                  showSolo && solo.blendedRate != null
                    ? `${formatDollars(solo.blendedRate)}/h blended`
                    : undefined
                }
              />
              {showSolo ? (
                <>
                  <HeadlineStat
                    label="Primary wages"
                    display={formatDollars(solo.primaryWages)}
                    hint="base rate + OT"
                  />
                  <HeadlineStat
                    label="Solo wages"
                    display={formatDollars(solo.soloWages)}
                    hint={
                      soloGap.length
                        ? `stale — ${soloGap.length} ${
                            soloGap.length === 1 ? "day" : "days"
                          } of solo hours behind punches`
                        : solo.people
                          ? `${formatHours(solo.soloHours)}h · ${formatCents(
                              solo.premiumCents,
                            )} over base`
                          : "no eligible solo hours this period"
                    }
                    // Stale solo hours understate this, they do not blank it, so
                    // it has to warn rather than just read low — a low number
                    // looks like a quiet fortnight (2026-09-20: 12.24h vs 20.16h).
                    hintWarn={soloGap.length > 0}
                  />
                </>
              ) : null}
              <HeadlineStat label="Tips" display={formatDollars(totalTips)} />
              <HeadlineStat
                label="Review bonus"
                display={formatDollars(totalBonus)}
              />
              <HeadlineStat
                label="Recognition"
                display={formatDollars(totalRecognition)}
              />
              <HeadlineStat label="Perks" display={formatDollars(totalPerks)} />
              <HeadlineStat
                label="Total pay"
                display={formatDollars(totalPay)}
                hint={showPreviewHints ? payVsPreview?.label : undefined}
                hintWarn={Boolean(
                  showPreviewHints && payVsPreview && !payVsPreview.match,
                )}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {showPreviewHints
                ? "Against last ADP Preview: Hours → Total hours, Total pay → Gross (wages + tips + bonus + perks). Total hours includes OT and solo hours — the OT and solo columns are slices of it, not extras to add. ADP's Enter-payroll Regular Hours column excludes OT, so it reads lower than Total hours by the OT figure. Preview URLs are not shown — they are session hashes. People and hours are 1:1 with Enter payroll. Open-biweek hours run through yesterday CT (not today). Zero-hour rows are people ADP still lists this run with no punches in that window. Wages is hours × rate only (blended across rate lines when solo hours exist). Taxes, Net pay, and Cash required are ADP-only."
                : awaitingEarnings
                  ? "Submitted in ADP. Earnings & Hours is not in BigQuery yet, so there is nothing to compare — Hours / Wages / Total pay are our estimate only. Wage vs ADP appears once that scrape lands."
                  : "People and hours are 1:1 with Enter payroll. Total hours includes OT and solo hours — the OT and solo columns are slices of it, not extras to add. Open-biweek hours run through yesterday CT (not today). Zero-hour rows are people ADP still lists this run with no punches in that window. Wages is hours × rate only (blended across rate lines when solo hours exist). Taxes, Net pay, and Cash required are ADP-only."}
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <h2 className="text-sm font-medium text-muted-foreground">
              Per-employee, per-period
            </h2>
            <DataTable
              columns={periodColumns}
              data={displayRows}
              pinLeft={["employee"]}
            />
            <p className="text-xs text-muted-foreground">
              {showPreviewHints
                ? "Wage vs ADP / Bonus vs ADP appear after Earnings & Hours lands for a paid period."
                : awaitingEarnings
                  ? "No Earnings & Hours rows yet — ADP paycheck diffs are hidden until that scrape, not shown as Not on ADP."
                  : "Wage vs ADP and Bonus vs ADP compare our estimate to Earnings & Hours. $0.00 = match. “Not on ADP” means they punched here but had no paycheck line that period (not a rate bug)."}
            </p>
            {showSolo ? (
              <p className="text-xs text-muted-foreground">
                Solo hrs are hours worked as the only person clocked in (a manager
                on the clock counts, and runs under the minimum block do not), shown
                only for employees at the eligible base rate on or after the
                effective date. Solo wages are those hours at the solo rate — the
                whole rate-2 line, not the uplift — and Primary wages are everything
                else at base rate, including overtime. The two add up to Est. wages,
                so Est. wages is a blended-rate figure whenever solo hours exist.
                Each is keyed into ADP as its own rate line per employee — see
                RUNBOOK § Solo-shift premium.
              </p>
            ) : null}
            {soloGap.length ? (
              <p className="text-xs text-amber-600 dark:text-amber-500">
                Solo hours are behind the punch data for{" "}
                {soloGap.includes("unknown")
                  ? "an unknown number of days"
                  : `${soloGap.join(", ")}`}
                , so the premium above is understated. Re-run{" "}
                <code className="font-mono">materialize_model_bq</code> before
                keying rate-2 into ADP.
              </p>
            ) : null}
            {showSolo ? (
              <p className="text-xs text-amber-600 dark:text-amber-500">
                Opening the ADP draft re-imports timecards, which restores every
                base row to its full total and leaves the solo rate-2 lines in
                place — overstating the draft by the solo hours above. Choose{" "}
                <span className="font-medium">Skip</span>, not “Import latest
                timecards”, and review the numbers here instead. If it has
                already happened, re-run the payroll draft to repair it rather
                than editing cells by hand.
              </p>
            ) : null}
          </div>

          {FEATURES.writeTipExemptions || shifts.length || exemptions.length ? (
            <TipExemptionsEditor
              shifts={shifts}
              exemptions={exemptions}
              employees={employees}
              editable={editable}
              periodLabel={periodLabel}
              refreshRunning={refreshRunning}
            />
          ) : null}

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Google review bonuses — last 30 days
            </h2>
            <DataTable columns={reviewColumns} data={reviews} />
          </div>

          <div className="flex flex-col gap-2">
            <h2 className="text-sm font-medium text-muted-foreground">
              Recognition bonuses — last 2 periods
            </h2>
            <DataTable columns={recognitionColumns} data={recognitions} />
          </div>
        </>
      )}
    </div>
  );
}
