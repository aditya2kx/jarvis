import { payrollPeriod, reviewBonusDetail } from "@/lib/bq/queries";
import { formatDollars } from "@/lib/format";
import { DataTable } from "@/components/tables/DataTable";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ColumnDef } from "@tanstack/react-table";
import type { PayrollPeriodRow, ReviewBonusDetailRow } from "@/lib/bq/queries";

export const revalidate = 600;

export default async function PayrollPage() {
  let periods: PayrollPeriodRow[] = [];
  let reviews: ReviewBonusDetailRow[] = [];
  let error: string | undefined;
  try {
    [periods, reviews] = await Promise.all([payrollPeriod(2), reviewBonusDetail(30)]);
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
        </>
      )}
    </div>
  );
}
