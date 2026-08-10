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
  { name: "moveRestockDateAction", page: "inventory", heavy: "order-reco" },
  { name: "removeRestockDateAction", page: "inventory", heavy: "order-reco" },
  { name: "setCapacityAction", page: "inventory", heavy: "order-reco" },
  { name: "setUsageDayOverrideAction", page: "inventory", heavy: "order-reco" },
  { name: "clearUsageDayOverrideAction", page: "inventory", heavy: "order-reco" },
  { name: "applyUsageDayOverridesAction", page: "inventory", heavy: "order-reco" },
  { name: "applyOrderTubOverridesAction", page: "inventory", heavy: "order-reco" },
  { name: "setCurrentQtyOverrideAction", page: "inventory", heavy: "order-reco" },
  { name: "clearCurrentQtyOverrideAction", page: "inventory", heavy: "order-reco" },
  { name: "applyCurrentQtyOverridesAction", page: "inventory", heavy: "order-reco" },
  { name: "clearCurrentQtyOverridesAction", page: "inventory", heavy: "order-reco" },
  { name: "pollOrderRecoRefreshAction", page: "inventory", heavy: null },
  // Payroll
  { name: "addTrainingShiftAction", page: "payroll", heavy: null },
  { name: "addRecognitionBonusAction", page: "payroll", heavy: null },
  { name: "applyTipExemptionsAction", page: "payroll", heavy: "model-recompute" },
  // Labor
  { name: "syncScheduledShiftsAction", page: "labor", heavy: "adp-schedule" },
  { name: "pollScheduledShiftsSyncAction", page: "labor", heavy: null },
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
  // Automations (Issue #216)
  { name: "saveTeamPulseConfigAction", page: "automations", heavy: null },
  { name: "previewTeamPulseAction", page: "automations", heavy: null },
  { name: "postTeamPulseOnceAction", page: "automations", heavy: null },
] as const;

export type MutatingActionName = (typeof MUTATING_ACTIONS)[number]["name"];
