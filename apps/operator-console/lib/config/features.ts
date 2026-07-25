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
  /** Issue #158/#160 — Accounting page (linked bank feed only). */
  accounting: true,
  writeGoals: true,
  /** Legacy single-row training quick-add — superseded by Tip Exemptions editor (Issue #167). */
  writeTraining: false,
  writeTipExemptions: true,
  writeRecognition: true,
  writeRestock: true,
  /** Issue #158 — Plaid Link + sync write path. */
  writePlaidLink: true,
  /**
   * Issue #175 — enqueue order-reco refresh via Cloud Run Job instead of
   * awaiting inline BQ TVFs on the click path. Brief Order Tubs staleness
   * until the job finishes; set false to restore sync refresh.
   */
  asyncOrderReco: true,
} as const;
