import {
  payrollPeriod,
  reviewBonusDetail,
  recognitionBonuses,
  adpShiftsForPeriod,
  tipExemptions,
  listCanonicalEmployees,
  listPayPeriodsWithPaidStatus,
  payrollDraftRun,
} from "@/lib/bq/queries";
import { formatDate, formatDollars, formatHours } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterSelect } from "@/components/filters/FilterSelect";
import { FilterMultiSelect } from "@/components/filters/FilterMultiSelect";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TrainingQuickAdd } from "@/components/drawers/TrainingQuickAdd";
import { RecognitionDrawer } from "@/components/drawers/RecognitionDrawer";
import { TipExemptionsEditor } from "@/components/drawers/TipExemptionsEditor";
import { FEATURES } from "@/lib/config/features";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import { payPeriodKey } from "@/lib/payroll/periodKey";
import { rowMatchesLaborType } from "@/lib/payroll/laborBucket";
import { PayrollDraftButton } from "@/components/payroll/PayrollDraftButton";
import { adpPayrollDetailsUrl } from "@/lib/payroll/adpLink";
import { previewLine } from "@/lib/payroll/previewDiff";
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
} from "@/lib/bq/queries";

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

  if (!error && selectedPeriodStart && periodEnd) {
    try {
      const [periodRowsAll, run] = await Promise.all([
        payrollPeriod(6),
        FEATURES.adpPayrollDraft
          ? payrollDraftRun(DEFAULT_STORE, selectedPeriodStart, periodEnd)
          : Promise.resolve(null),
      ]);
      periods = periodRowsAll;
      draftRun = run;
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

  const tipStart = selectedPeriodStart;
  const tipEnd = periodEnd;
  const editable = FEATURES.writeTipExemptions && selectedUnpaid;

  if (!error && tipStart && tipEnd) {
    try {
      const [s, e, empRows] = await Promise.all([
        adpShiftsForPeriod(DEFAULT_STORE, tipStart, tipEnd),
        tipExemptions(DEFAULT_STORE, tipStart, tipEnd),
        listCanonicalEmployees(DEFAULT_STORE),
      ]);
      shifts = s;
      exemptions = e;
      employees = empRows.map((r) => r.employee_name);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  const totalHours = periodRows.reduce((s, p) => s + (p.hours_worked ?? 0), 0);
  const totalPay = periodRows.reduce((s, p) => s + (p.est_total_pay ?? 0), 0);
  const totalWages = periodRows.reduce((s, p) => s + (p.est_gross_pay ?? 0), 0);
  const totalTips = periodRows.reduce((s, p) => s + (p.tips_allocated ?? 0), 0);
  const totalBonus = periodRows.reduce((s, p) => s + (p.review_bonus ?? 0), 0);
  const totalRecognition = periodRows.reduce(
    (s, p) => s + (Number(p.recognition_bonus) || 0),
    0,
  );
  const totalPerks = periodRows.reduce((s, p) => s + (Number(p.perks) || 0), 0);
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

  const periodColumns: ColumnDef<PayrollPeriodRow>[] = [
    { accessorKey: "employee", header: "Employee" },
    { accessorKey: "wage_rate_dollars", header: "Rate", meta: { format: { kind: "dollars" } } },
    { accessorKey: "hours_worked", header: "Hours", meta: { format: { kind: "number", digits: 2, minDigits: 2 } } },
    { accessorKey: "ot_hours", header: "OT", meta: { format: { kind: "number", digits: 2, minDigits: 2 } } },
    { accessorKey: "est_gross_pay", header: "Est. wages", meta: { format: { kind: "dollars" } } },
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
    ...(selectedUnpaid
      ? []
      : [
          {
            accessorKey: "wage_diff",
            header: "Wage vs ADP",
            meta: {
              format: { kind: "adp_diff" as const, paidKey: "adp_wages_paid" },
            },
          } satisfies ColumnDef<PayrollPeriodRow>,
          {
            accessorKey: "bonus_diff",
            header: "Bonus vs ADP",
            meta: {
              format: { kind: "adp_diff" as const, paidKey: "adp_bonus_paid" },
            },
          } satisfies ColumnDef<PayrollPeriodRow>,
        ]),
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
    }${o.unpaid ? "Unpaid" : "Paid (ADP)"}`,
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
                historicPayrollUrl={
                  selectedUnpaid ? null : adpPayrollDetailsUrl()
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
            {FEATURES.writeTraining ? <TrainingQuickAdd /> : null}
            {FEATURES.writeRecognition ? (
              <RecognitionDrawer
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
              {selectedUnpaid ? " · Unpaid (ADP)" : " · Paid (ADP)"}
              {editable ? " · tip exemptions editable" : ""}
            </p>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
              <HeadlineStat
                label="Hours"
                display={`${formatHours(totalHours)}h`}
                hint={selectedUnpaid ? hoursVsPreview?.label : undefined}
                hintWarn={Boolean(hoursVsPreview && !hoursVsPreview.match)}
              />
              <HeadlineStat label="Wages" display={formatDollars(totalWages)} />
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
                hint={selectedUnpaid ? payVsPreview?.label : undefined}
                hintWarn={Boolean(payVsPreview && !payVsPreview.match)}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Against last ADP Preview: Hours → Total hours, Total pay → Gross
              (wages + tips + bonus + perks). Preview URLs are not shown — they
              are session hashes. People and hours are 1:1 with Enter payroll.
              Open-biweek hours run through yesterday CT (not today). Zero-hour
              rows are people ADP still lists this run with no punches in that
              window. Wages is hours × rate only. Taxes, Net pay, and Cash
              required are ADP-only.
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <h2 className="text-sm font-medium text-muted-foreground">
              Per-employee, per-period
            </h2>
            <DataTable
              columns={periodColumns}
              data={periodRows}
              pinLeft={["employee"]}
            />
            <p className="text-xs text-muted-foreground">
              {selectedUnpaid
                ? "Wage vs ADP / Bonus vs ADP appear after the period is paid. Scroll sideways for later columns."
                : "Wage vs ADP and Bonus vs ADP compare our estimate to Earnings & Hours. $0.00 = match. “Not on ADP” means they punched here but had no paycheck line that period (not a rate bug)."}
            </p>
          </div>

          {FEATURES.writeTipExemptions || shifts.length || exemptions.length ? (
            <TipExemptionsEditor
              shifts={shifts}
              exemptions={exemptions}
              employees={employees}
              editable={editable}
              periodLabel={periodLabel}
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
