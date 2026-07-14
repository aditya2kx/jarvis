// Read screens ship in M1/M2 and stay on; write paths flip on per-milestone
// as their MERGE contracts land (M3/M4). See docs/operator-console/EXECUTION.md §4.
export const FEATURES = {
  sales: true,
  labor: true,
  forecast: true,
  orderQuality: true,
  inventory: true,
  payroll: true,
  pipeline: true,
  /** Issue #158 — Accounting page (Square in / Plaid out). */
  accounting: true,
  writeGoals: true,
  /** Legacy single-row training quick-add — superseded by Tip Exemptions editor (Issue #167). */
  writeTraining: false,
  writeTipExemptions: true,
  writeRecognition: true,
  writeRestock: true,
  /** Issue #158 — Plaid Link + sync write path. */
  writePlaidLink: true,
} as const;
