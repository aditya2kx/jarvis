/**
 * Accounting + distinctness:
 * - Money in / gain → green family
 * - Money out / loss → red family
 * - Cash-flow net → sky (gain) / fuchsia (loss) so it never blends with in/out
 * - Expense categories → wide hue spacing (not adjacent reds/oranges)
 */

export const ACCOUNTING_COLORS = {
  moneyIn: "#22c55e", // green-500 — deposits
  moneyOut: "#ef4444", // red-500 — spend
  /** Net cash flow — deliberately NOT green/red so bars stay readable vs in/out. */
  cashFlowGain: "#38bdf8", // sky-400 — positive net (excitement without matching money-in)
  cashFlowLoss: "#e879f9", // fuchsia-400 — negative net (alarm without matching money-out)
  /**
   * Spend-by-category: high-chroma, ~45°+ hue steps so 5–6 series stay separable
   * on dark and light backgrounds. Warm-first so it still reads as “money leaving.”
   */
  expenseCategories: [
    "#e11d48", // rose
    "#f97316", // orange
    "#eab308", // yellow
    "#14b8a6", // teal
    "#3b82f6", // blue
    "#8b5cf6", // violet
    "#ec4899", // pink
    "#64748b", // slate — Other (last)
  ],
} as const;

/** @deprecated Prefer ACCOUNTING_COLORS / expenseCategoryColor. */
export const DISTINCT_CHART_COLORS = ACCOUNTING_COLORS.expenseCategories;

export function chartColorAt(index: number): string {
  const palette = ACCOUNTING_COLORS.expenseCategories;
  return palette[index % palette.length]!;
}

/** Labor hours / concurrent — actual vs scheduled (Issue #213). */
export const LABOR_CHART_COLORS = {
  parttimeActual: chartColorAt(0),
  fulltimeActual: chartColorAt(1),
  /** Distinct slate family so future schedule never looks like clocked hours. */
  parttimeScheduled: "#64748b", // slate-500
  fulltimeScheduled: "#94a3b8", // slate-400
  /** Goal reference line — gold, not red (PT bars already use rose). */
  goalLine: "#eab308", // yellow-500
} as const;

export function expenseCategoryColor(index: number, isOther = false): string {
  const palette = ACCOUNTING_COLORS.expenseCategories;
  if (isOther) return palette[palette.length - 1]!;
  return palette[index % (palette.length - 1)]!;
}
