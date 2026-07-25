/**
 * Canonical inventory of every mutating server action in the operator console.
 * scripts/check_operator_console_actions.py fails if an exported *Action
 * under apps/operator-console/app/.../actions.ts is missing from this list
 * (future-proof gate).
 */
export const MUTATING_ACTIONS = [
  // Home
  { name: "saveGoalsAction", page: "home", heavy: null },
  { name: "saveGoalAction", page: "home", heavy: null },
  // Inventory
  { name: "submitRestockAction", page: "inventory", heavy: "order-reco" },
  { name: "replaceEstimatedRestockDateAction", page: "inventory", heavy: "order-reco" },
  { name: "setCapacityAction", page: "inventory", heavy: "order-reco" },
  // Payroll
  { name: "addTrainingShiftAction", page: "payroll", heavy: null },
  { name: "addRecognitionBonusAction", page: "payroll", heavy: null },
  { name: "applyTipExemptionsAction", page: "payroll", heavy: "model-recompute" },
  // Accounting
  { name: "createPlaidLinkTokenAction", page: "accounting", heavy: null },
  { name: "exchangePlaidPublicTokenAction", page: "accounting", heavy: "plaid-sync" },
  { name: "syncPlaidNowAction", page: "accounting", heavy: "plaid-sync" },
  { name: "setPlaidInternalAction", page: "accounting", heavy: null },
  { name: "reapplyPlaidCategoriesAction", page: "accounting", heavy: null },
  { name: "setTxnCategoryOverrideAction", page: "accounting", heavy: null },
  { name: "upsertTaxonomyNodeAction", page: "accounting", heavy: null },
  { name: "setTaxonomyNodeEnabledAction", page: "accounting", heavy: null },
  { name: "setCategoryRuleEnabledAction", page: "accounting", heavy: null },
  { name: "dryRunRuleAction", page: "accounting", heavy: null },
  { name: "previewRuleMatchesAction", page: "accounting", heavy: null },
  { name: "commitRuleFromTxnAction", page: "accounting", heavy: null },
  { name: "revertRuleEvidenceAction", page: "accounting", heavy: null },
  { name: "setTaxonomyExcludeAction", page: "accounting", heavy: null },
] as const;

export type MutatingActionName = (typeof MUTATING_ACTIONS)[number]["name"];
