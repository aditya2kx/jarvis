import {
  payrollPeriod,
  reviewBonusDetail,
  recognitionBonuses,
  adpShiftsForPeriod,
  tipExemptions,
  listCanonicalEmployees,
  openPayPeriodBounds,
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
} from "@/lib/bq/queries";

export const dynamic = "force-dynamic";

type View = "reconciliation" | "detail";

function parseView(value: string | string[] | undefined): View {
  return (Array.isArray(value) ? value[0] : value) === "detail" ? "detail" : "reconciliation";
}

function parsePeriod(value: string | string[] | undefined): "current" | "last" {
  return (Array.isArray(value) ? value[0] : value) === "last" ? "last" : "current";
}

export default async function PayrollPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string; period?: string }>;
}) {
  const sp = await searchParams;
  const view = parseView(sp.view);
  const period = parsePeriod(sp.period);

  let periods: PayrollPeriodRow[] = [];
  let reviews: ReviewBonusDetailRow[] = [];
  let recognitions: RecognitionBonusRow[] = [];
  let shifts: AdpShiftRow[] = [];
  let exemptions: TipExemptionRow[] = [];
  let employees: string[] = [];
  let error: string | undefined;
  try {
    [periods, reviews, recognitions] = await Promise.all([
      payrollPeriod(2),
      reviewBonusDetail(30),
      recognitionBonuses(DEFAULT_STORE, 2),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const periodStarts = Array.from(new Set(periods.map((p) => p.period_start)));
  const selectedPeriodStart = period === "last" ? periodStarts[1] : periodStarts[0];
  const periodRows = selectedPeriodStart
    ? periods.filter((p) => p.period_start === selectedPeriodStart)
    : periods;

  const periodEnd = periodRows[0]?.period_end;
  const modelIsOpen = periodRows.some((p) => p.is_open);

  // Tip-exemption edit window: BQ is_open rows, or calendar open period when
  // the model has not materialized an open window yet (day-after close / orphans).
  let openBounds: { start: string; end: string } | null = null;
  if (!error && FEATURES.writeTipExemptions) {
    try {
      openBounds = await openPayPeriodBounds();
    } catch {
      openBounds = null;
    }
  }
  const tipStart =
    period === "current" && openBounds ? openBounds.start : selectedPeriodStart;
  const tipEnd = period === "current" && openBounds ? openBounds.end : periodEnd;
  const editable =
    FEATURES.writeTipExemptions && period === "current" && Boolean(openBounds);
  const isOpen = modelIsOpen || (period === "current" && Boolean(openBounds));

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
  const tipPeriodLabel =
    tipStart && tipEnd ? `${formatDate(tipStart)} – ${formatDate(tipEnd)}` : periodLabel;

  const periodColumns: ColumnDef<PayrollPeriodRow>[] = [
    { accessorKey: "period_start", header: "Period start", meta: { format: { kind: "date" } } },
    { accessorKey: "period_end", header: "Period end", meta: { format: { kind: "date" } } },
    { accessorKey: "employee", header: "Employee" },
    { accessorKey: "hours_worked", header: "Hours", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "est_gross_pay", header: "Est. wages", meta: { format: { kind: "dollars" } } },
    { accessorKey: "tips_allocated", header: "Tips", meta: { format: { kind: "dollars" } } },
    { accessorKey: "review_bonus", header: "Review bonus", meta: { format: { kind: "dollars" } } },
    { accessorKey: "est_total_pay", header: "Est. total", meta: { format: { kind: "dollars" } } },
    {
      accessorKey: "wage_diff",
      header: "Wage diff (est-ADP)",
      meta: {
        format: {
          kind: "dollars",
          thresholds: { warn: 50, bad: 150, direction: "higher-bad", useAbs: true },
        },
      },
    },
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

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Payroll & People"
        subtitle={`Wages, tips, bonuses, and tip exemptions · ${storeDisplayName(DEFAULT_STORE)}`}
        right={
          <>
            <FilterPills
              label="Period"
              param="period"
              value={period}
              options={[
                { value: "current", label: "Current" },
                { value: "last", label: "Last" },
              ]}
              basePath="/payroll"
              extraParams={{ view }}
            />
            <FilterPills
              label="View"
              param="view"
              value={view}
              options={[
                { value: "reconciliation", label: "Reconciliation" },
                { value: "detail", label: "Detail" },
              ]}
              basePath="/payroll"
              extraParams={{ period }}
            />
            {FEATURES.writeTraining ? <TrainingQuickAdd /> : null}
            {FEATURES.writeRecognition ? (
              <RecognitionDrawer defaultPayPeriod={periods[0]?.period_start ?? ""} />
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
              {period === "current" ? "Current" : "Last"} pay period
              {selectedPeriodStart && periodEnd ? ` · ${periodLabel}` : ""}
              {modelIsOpen
                ? " · open"
                : period === "current" && openBounds
                  ? ` · tip exemptions ${tipPeriodLabel}`
                  : " · closed"}
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

          {view === "reconciliation" ? (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-medium text-muted-foreground">
                Per-employee, per-period detail
              </h2>
              <DataTable columns={periodColumns} data={periodRows} />
            </div>
          ) : (
            <>
              <div>
                <h2 className="mb-2 text-sm font-medium text-muted-foreground">
                  Google review bonuses — last 30 days
                </h2>
                <DataTable columns={reviewColumns} data={reviews} />
              </div>

              {FEATURES.writeTipExemptions || shifts.length || exemptions.length ? (
                <TipExemptionsEditor
                  shifts={shifts}
                  exemptions={exemptions}
                  employees={employees}
                  editable={editable}
                  periodLabel={tipPeriodLabel}
                />
              ) : null}

              <div className="flex flex-col gap-2">
                <h2 className="text-sm font-medium text-muted-foreground">
                  Recognition bonuses — last 2 periods
                </h2>
                <DataTable columns={recognitionColumns} data={recognitions} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
