/**
 * Labor cost lenses (Issue #166): three mutually exclusive views so operators
 * compare wage / paid-payroll / blended-schedule without mixed labels.
 *
 * - wage: completed days only, hourly wages
 * - paid: completed days only, wage × (1 + labor_burden_pct)
 * - blended: completed wage + remaining scheduled days (wage only; no burden)
 */
import type { FilterOption } from "@/components/filters/FilterPills";
import type { LaborForwardSummary } from "@/lib/kpi/labor-forward";

export type LaborLens = "wage" | "paid" | "blended";

export const LABOR_LENS_OPTIONS: FilterOption[] = [
  { value: "wage", label: "Wage" },
  { value: "paid", label: "Paid payroll" },
  { value: "blended", label: "Blended (schedule)" },
];

export function parseLaborLens(raw: string | undefined | null): LaborLens {
  if (raw === "paid" || raw === "blended" || raw === "wage") return raw;
  return "wage";
}

/** Inclusive calendar days in a Period window (YYYY-MM-DD). */
export function periodDayCount(start: string, end: string): number {
  const s = Date.parse(`${start}T12:00:00Z`);
  const e = Date.parse(`${end}T12:00:00Z`);
  if (!Number.isFinite(s) || !Number.isFinite(e) || e < s) return 0;
  return Math.round((e - s) / 86_400_000) + 1;
}

/**
 * Human coverage line for wage $ context — e.g. "15 completed + 11 scheduled
 * = 26 days (84% of 31-day Period · ≈3.7 weeks)".
 */
export function laborCoverageLabel(
  data: LaborForwardSummary,
  lens: LaborLens,
  periodDays = 0,
): string {
  const completed = data.hasCompleted ? data.completedDayCount : 0;
  const scheduled = data.hasForward ? data.fwdDays : 0;

  const withPeriod = (covered: number, core: string): string => {
    if (!(periodDays > 0) || covered <= 0) return core;
    const pct = Math.round((covered / periodDays) * 100);
    const weeks = (covered / 7).toFixed(1);
    return `${core} (${pct}% of ${periodDays}-day Period · ≈${weeks} weeks)`;
  };

  if (lens === "blended") {
    const covered = completed + scheduled;
    const core =
      scheduled > 0
        ? `${completed} completed + ${scheduled} scheduled = ${covered} days`
        : `${completed} completed day${completed === 1 ? "" : "s"} (no forward schedule in Period)`;
    return withPeriod(covered, core);
  }

  const core = `${completed} completed day${completed === 1 ? "" : "s"}`;
  return withPeriod(completed, core);
}

export interface LaborLensView {
  lens: LaborLens;
  title: string;
  description: string;
  /** Short coverage line shown under $ amounts. */
  coverage: string;
  ptPct: number | null;
  ptDollars: number | null;
  totalPct: number | null;
  totalDollars: number | null;
  ptLabel: string;
  totalLabel: string;
  /** True when paid lens but burden unset — show empty paid state. */
  paidUnavailable: boolean;
}

export function viewForLaborLens(
  data: LaborForwardSummary,
  lens: LaborLens,
  periodDays = 0,
): LaborLensView {
  const coverage = laborCoverageLabel(data, lens, periodDays);

  if (lens === "paid") {
    const paidUnavailable = !(data.laborBurdenPct > 0);
    return {
      lens,
      title: "Paid payroll — completed",
      description: paidUnavailable
        ? "Set store_config.labor_burden_pct to show wage + employer load (ER tax)."
        : `Completed days only. Wage × (1 + ${(data.laborBurdenPct * 100).toFixed(0)}% ER burden from ADP Payroll Liability). ${coverage}.`,
      coverage,
      ptPct: paidUnavailable ? null : data.completedPtPctAllIn,
      ptDollars: paidUnavailable ? null : data.completedPtCostAllIn,
      totalPct: paidUnavailable ? null : data.completedTotalPctAllIn,
      totalDollars: paidUnavailable ? null : data.completedTotalCostAllIn,
      ptLabel: "Part-time",
      totalLabel: "Total (PT + FT)",
      paidUnavailable,
    };
  }

  if (lens === "blended") {
    const parts = [
      data.avgPtWage != null ? `avg PT wage $${data.avgPtWage.toFixed(2)}` : null,
      data.fwdDays > 0 ? `${data.fwdScheduledHours.toFixed(1)} scheduled hrs` : null,
      data.aov != null ? `AOV $${data.aov.toFixed(2)}` : null,
    ].filter(Boolean);
    return {
      lens,
      title: "Blended — period incl. schedule",
      description:
        `Wage only. Completed punches + remaining ADP-scheduled days (no fill for unscheduled days). ${coverage}${parts.length ? ` · ${parts.join(" · ")}` : ""}.`,
      coverage,
      ptPct: data.hasForward || data.hasCompleted ? data.projectedPtPct : null,
      ptDollars: data.hasForward || data.hasCompleted ? data.projectedPtCost : null,
      totalPct: data.hasForward || data.hasCompleted ? data.projectedTotalPct : null,
      totalDollars: data.hasForward || data.hasCompleted ? data.projectedTotalCost : null,
      ptLabel: "Part-time",
      totalLabel: "Total (PT + FT)",
      paidUnavailable: false,
    };
  }

  return {
    lens: "wage",
    title: "Wage — completed",
    description: `Completed days only. Hourly + salaried wage cost ÷ net sales (no employer taxes). ${coverage}.`,
    coverage,
    ptPct: data.hasCompleted ? data.completedPtPct : null,
    ptDollars: data.hasCompleted ? data.completedPtCost : null,
    totalPct: data.hasCompleted ? data.completedTotalPct : null,
    totalDollars: data.hasCompleted ? data.completedTotalCost : null,
    ptLabel: "Part-time",
    totalLabel: "Total (PT + FT)",
    paidUnavailable: false,
  };
}
