// Read screens ship in M1/M2 and stay on; write paths flip on per-milestone
// as their MERGE contracts land (M3/M4). See docs/operator-console/EXECUTION.md §4.
export const FEATURES = {
  sales: true,
  labor: true,
  /** Issue #213 — Forecast stripped from Operator Console (BQ/Grafana pipeline kept). */
  forecast: false,
  orderQuality: true,
  inventory: true,
  payroll: true,
  /**
   * Issue #251 — Playwright ADP RUN Start→Preview; leave draft. Never Approve/Save.
   * Console /payroll button (unpaid periods) + Monday 07:00 CT
   * ``bhaga-payroll-draft`` scheduler. Cloud Run job env stays unset so
   * ``bhaga-nightly`` 21:30 CT never Starts payroll. Operator submits in ADP.
   */
  adpPayrollDraft: true,
  pipeline: true,
  /** Issue #158/#160 — Accounting page (linked bank feed only). */
  accounting: true,
  writeGoals: true,
  /** Legacy single-row training quick-add — superseded by Tip Exemptions editor (Issue #167). */
  writeTraining: false,
  writeTipExemptions: true,
  writeRecognition: true,
  /** Issue #267 — mileage/gym/cert MERGE into employee_perks (period or recurring). */
  writePerks: true,
  writeRestock: true,
  /**
   * Issue #194 — per-day force include/exclude on Base usage audit table.
   * Changes avg/day + order reco; flag-off = read-only chips.
   */
  writeInventoryDayOverrides: true,
  /** Issue #158 — Plaid Link + sync write path. */
  writePlaidLink: true,
  /**
   * Issue #175 — enqueue order-reco refresh via Cloud Run Job instead of
   * awaiting inline BQ TVFs on the click path. Brief Order Tubs staleness
   * until the job finishes; set false to restore sync refresh.
   */
  asyncOrderReco: true,
} as const;
