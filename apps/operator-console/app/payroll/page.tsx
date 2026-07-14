import {
  payrollPeriod,
  reviewBonusDetail,
  recognitionBonuses,
  adpShiftsForPeriod,
  tipExemptions,
  listCanonicalEmployees,
  openPayPeriodBounds,
  listPayPeriodsWithPaidStatus,
} from "@/lib/bq/queries";
import { formatDate, formatDollars } from "@/lib/format";
import { storeDisplayName } from "@/lib/config/stores";
import { DataTable } from "@/components/tables/DataTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { FilterPills } from "@/components/filters/FilterPills";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TrainingQuickAdd } from "@/components/drawers/TrainingQuickAdd";
import { RecognitionDrawer } from "@/components/drawers/RecognitionDrawer";
import { TipExemptionsEditor } from "@/components/drawers/TipExemptionsEditor";
import { FEATURES } from "@/lib/config/features";
import { DEFAULT_STORE } from "@/lib/auth/identity";
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

function parsePeriodStart(
  value: string | string[] | undefined,
  options: PayPeriodOption[],
  unpaidBounds: { start: string; end: string } | null,
): string | null {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw && options.some((o) => o.period_start === raw)) return raw;
  const defaultUnpaid = options.find((o) => o.unpaid);
  if (defaultUnpaid) return defaultUnpaid.period_start;
  if (unpaidBounds && options.some((o) => o.period_start === unpaidBounds.start)) {
    return unpaidBounds.start;
  }
  return options[0]?.period_start ?? null;
}

export default async function PayrollPage({
  searchParams,
}: {
  searchParams: Promise<{ period?: string }>;
}) {
  const sp = await searchParams;

  let periods: PayrollPeriodRow[] = [];
  let periodOptions: PayPeriodOption[] = [];
  let reviews: ReviewBonusDetailRow[] = [];
  let recognitions: RecognitionBonusRow[] = [];
  let shifts: AdpShiftRow[] = [];
  let exemptions: TipExemptionRow[] = [];
  let employees: string[] = [];
  let unpaidBounds: { start: string; end: string } | null = null;
  let error: string | undefined;
  try {
    const settled = await Promise.all([
      listPayPeriodsWithPaidStatus(6),
      reviewBonusDetail(30),
      recognitionBonuses(DEFAULT_STORE, 2),
      FEATURES.writeTipExemptions ? openPayPeriodBounds() : Promise.resolve(null),
    ]);
    periodOptions = settled[0];
    reviews = settled[1];
    recognitions = settled[2];
    unpaidBounds = settled[3];
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const selectedPeriodStart = parsePeriodStart(sp.period, periodOptions, unpaidBounds);
  const selectedOpt = periodOptions.find((o) => o.period_start === selectedPeriodStart);
  const periodEnd = selectedOpt?.period_end;
  const selectedUnpaid = Boolean(selectedOpt?.unpaid);

  if (!error && selectedPeriodStart && periodEnd) {
    try {
      periods = await payrollPeriod(6);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  const periodRows =
    selectedPeriodStart && periods.length
      ? periods.filter((p) => p.period_start === selectedPeriodStart)
      : [];

  const tipStart = selectedPeriodStart;
  const tipEnd = periodEnd;
  const editable =
    FEATURES.writeTipExemptions && selectedUnpaid && Boolean(unpaidBounds) &&
    tipStart === unpaidBounds?.start &&
    tipEnd === unpaidBounds?.end;

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

  const totalPay = periodRows.reduce((s, p) => s + (p.est_total_pay ?? 0), 0);
  const totalWages = periodRows.reduce((s, p) => s + (p.est_gross_pay ?? 0), 0);
  const totalBonus = periodRows.reduce((s, p) => s + (p.review_bonus ?? 0), 0);

  const periodLabel =
    selectedPeriodStart && periodEnd
      ? `${formatDate(selectedPeriodStart)} – ${formatDate(periodEnd)}`
      : "—";

  const periodColumns: ColumnDef<PayrollPeriodRow>[] = [
    { accessorKey: "period_start", header: "Period start", meta: { format: { kind: "date" } } },
    { accessorKey: "period_end", header: "Period end", meta: { format: { kind: "date" } } },
    { accessorKey: "employee", header: "Employee" },
    { accessorKey: "hours_worked", header: "Hours", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "est_gross_pay", header: "Est. wages", meta: { format: { kind: "dollars" } } },
    { accessorKey: "tips_allocated", header: "Tips", meta: { format: { kind: "dollars" } } },
    { accessorKey: "review_bonus", header: "Review bonus", meta: { format: { kind: "dollars" } } },
    { accessorKey: "est_total_pay", header: "Est. total", meta: { format: { kind: "dollars" } } },
    ...(selectedUnpaid
      ? []
      : [
          {
            accessorKey: "wage_diff",
            header: "Wage diff (est-ADP)",
            meta: {
              format: {
                kind: "dollars" as const,
                thresholds: {
                  warn: 50,
                  bad: 150,
                  direction: "higher-bad" as const,
                  useAbs: true,
                },
              },
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

  const periodPillOptions = periodOptions.map((o) => ({
    value: o.period_start,
    label: `${formatDate(o.period_start)} – ${formatDate(o.period_end)} · ${
      o.unpaid ? "Unpaid" : "Paid (ADP)"
    }`,
  }));

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Payroll & People"
        subtitle={`Wages, tips, bonuses, and tip exemptions · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            {periodPillOptions.length ? (
              <FilterPills
                label="Period"
                param="period"
                value={selectedPeriodStart ?? periodPillOptions[0].value}
                options={periodPillOptions}
                basePath="/payroll"
              />
            ) : null}
            {FEATURES.writeTraining ? <TrainingQuickAdd /> : null}
            {FEATURES.writeRecognition ? (
              <RecognitionDrawer defaultPayPeriod={selectedPeriodStart ?? ""} />
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
              {selectedUnpaid ? " · Unpaid (ADP)" : " · Paid (ADP)"}
              {editable ? " · tip exemptions editable" : ""}
            </p>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Total pay
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-semibold">{formatDollars(totalPay)}</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Wages
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-semibold">{formatDollars(totalWages)}</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Review bonus
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-semibold">{formatDollars(totalBonus)}</p>
                </CardContent>
              </Card>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <h2 className="text-sm font-medium text-muted-foreground">
              Per-employee, per-period
            </h2>
            <DataTable columns={periodColumns} data={periodRows} />
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
