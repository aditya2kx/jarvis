import { payrollPeriod, reviewBonusDetail, trainingShifts, recognitionBonuses } from "@/lib/bq/queries";
import { formatDollars } from "@/lib/format";
import { DataTable } from "@/components/tables/DataTable";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TrainingQuickAdd } from "@/components/drawers/TrainingQuickAdd";
import { RecognitionDrawer } from "@/components/drawers/RecognitionDrawer";
import { FEATURES } from "@/lib/config/features";
import { DEFAULT_STORE } from "@/lib/auth/identity";
import type { ColumnDef } from "@tanstack/react-table";
import type {
  PayrollPeriodRow,
  ReviewBonusDetailRow,
  TrainingShiftRow,
  RecognitionBonusRow,
} from "@/lib/bq/queries";

export const revalidate = 600;

export default async function PayrollPage() {
  let periods: PayrollPeriodRow[] = [];
  let reviews: ReviewBonusDetailRow[] = [];
  let training: TrainingShiftRow[] = [];
  let recognitions: RecognitionBonusRow[] = [];
  let error: string | undefined;
  try {
    [periods, reviews, training, recognitions] = await Promise.all([
      payrollPeriod(2),
      reviewBonusDetail(30),
      trainingShifts(DEFAULT_STORE, 30),
      recognitionBonuses(DEFAULT_STORE, 2),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const totalWages = periods.reduce((s, p) => s + (p.est_gross_pay ?? 0), 0);
  const totalBonus = periods.reduce((s, p) => s + (p.review_bonus ?? 0), 0);
  const totalWageDiff = periods.reduce((s, p) => s + (p.wage_diff ?? 0), 0);

  const periodColumns: ColumnDef<PayrollPeriodRow>[] = [
    { accessorKey: "period_start", header: "Period", meta: { format: { kind: "date" } } },
    { accessorKey: "employee", header: "Employee" },
    { accessorKey: "hours_worked", header: "Hours", meta: { format: { kind: "number", digits: 1 } } },
    { accessorKey: "est_gross_pay", header: "Est. wages", meta: { format: { kind: "dollars" } } },
    { accessorKey: "tips_allocated", header: "Tips", meta: { format: { kind: "dollars" } } },
    { accessorKey: "review_bonus", header: "Review bonus", meta: { format: { kind: "dollars" } } },
    { accessorKey: "est_total_pay", header: "Est. total", meta: { format: { kind: "dollars" } } },
    { accessorKey: "wage_diff", header: "Wage diff (est-ADP)", meta: { format: { kind: "dollars" } } },
  ];

  const reviewColumns: ColumnDef<ReviewBonusDetailRow>[] = [
    { accessorKey: "post_date_ct", header: "Posted", meta: { format: { kind: "date" } } },
    { accessorKey: "reviewer", header: "Reviewer" },
    { accessorKey: "rating", header: "Rating" },
    { accessorKey: "total_bonus", header: "Total bonus", meta: { format: { kind: "dollars" } } },
    { accessorKey: "employees_considered", header: "Employees" },
  ];

  const trainingColumns: ColumnDef<TrainingShiftRow>[] = [
    { accessorKey: "date", header: "Date", meta: { format: { kind: "date" } } },
    { accessorKey: "employee_name", header: "Employee" },
    { accessorKey: "note", header: "Note" },
  ];

  const recognitionColumns: ColumnDef<RecognitionBonusRow>[] = [
    { accessorKey: "pay_period", header: "Pay period" },
    { accessorKey: "employee", header: "Employee" },
    { accessorKey: "amount_cents", header: "Amount", meta: { format: { kind: "cents" } } },
    { accessorKey: "reason", header: "Reason" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold tracking-tight">Payroll &amp; People</h1>

      {error ? (
        <p className="text-sm text-muted-foreground">Data unavailable: {error}</p>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Est. wages (last 2 periods)
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{formatDollars(totalWages)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Review bonuses
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{formatDollars(totalBonus)}</p>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Wage diff vs ADP actual
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-semibold">{formatDollars(totalWageDiff)}</p>
              </CardContent>
            </Card>
          </div>

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Per-employee, per-period detail
            </h2>
            <DataTable columns={periodColumns} data={periods} />
          </div>

          <div>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              Google review bonuses — last 30 days
            </h2>
            <DataTable columns={reviewColumns} data={reviews} />
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-muted-foreground">
                Training shifts — last 30 days
              </h2>
              {FEATURES.writeTraining ? <TrainingQuickAdd /> : null}
            </div>
            <DataTable columns={trainingColumns} data={training} />
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-medium text-muted-foreground">
                Recognition bonuses — last 2 periods
              </h2>
              {FEATURES.writeRecognition ? (
                <RecognitionDrawer defaultPayPeriod={periods[0]?.period_start ?? ""} />
              ) : null}
            </div>
            <DataTable columns={recognitionColumns} data={recognitions} />
          </div>
        </>
      )}
    </div>
  );
}
