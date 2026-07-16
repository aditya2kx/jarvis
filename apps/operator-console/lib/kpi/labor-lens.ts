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

export interface LaborLensView {
  lens: LaborLens;
  title: string;
  description: string;
  ptPct: number | null;
  ptDollars: number | null;
  totalPct: number | null;
  totalDollars: number | null;
  ptLabel: string;
  totalLabel: string;
  /** True when paid lens but burden unset — show empty paid state. */
  paidUnavailable: boolean;
}

export function viewForLaborLens(data: LaborForwardSummary, lens: LaborLens): LaborLensView {
  if (lens === "paid") {
    const paidUnavailable = !(data.laborBurdenPct > 0);
    return {
      lens,
      title: "Paid payroll — completed",
      description: paidUnavailable
        ? "Set store_config.labor_burden_pct to show wage + employer load (ER tax)."
        : `Completed days only. Wage × (1 + ${(data.laborBurdenPct * 100).toFixed(0)}% ER burden from ADP Payroll Liability).`,
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
      data.fwdDays > 0
        ? `${data.fwdDays} scheduled day${data.fwdDays === 1 ? "" : "s"} (${data.fwdScheduledHours.toFixed(1)} hrs)`
        : "no upcoming schedule in period",
      data.aov != null ? `AOV $${data.aov.toFixed(2)}` : null,
    ].filter(Boolean);
    return {
      lens,
      title: "Blended — period incl. schedule",
      description:
        `Wage only. Completed punches + remaining ADP-scheduled days (no fill for unscheduled days). ${parts.join(" · ")}`,
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
    description: "Completed days only. Hourly + salaried wage cost ÷ net sales (no employer taxes).",
    ptPct: data.hasCompleted ? data.completedPtPct : null,
    ptDollars: data.hasCompleted ? data.completedPtCost : null,
    totalPct: data.hasCompleted ? data.completedTotalPct : null,
    totalDollars: data.hasCompleted ? data.completedTotalCost : null,
    ptLabel: "Part-time",
    totalLabel: "Total (PT + FT)",
    paidUnavailable: false,
  };
}
