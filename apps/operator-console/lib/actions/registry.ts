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
  { name: "orderRecoStatusAction", page: "inventory", heavy: null },
  { name: "retryOrderRecoAction", page: "inventory", heavy: "order-reco" },
  // Payroll
  { name: "addTrainingShiftAction", page: "payroll", heavy: null },
  { name: "addRecognitionBonusAction", page: "payroll", heavy: null },
  { name: "applyTipExemptionsAction", page: "payroll", heavy: "model-recompute" },
  { name: "runPayrollDraftAction", page: "payroll", heavy: "adp-payroll-draft" },
  { name: "pollPayrollDraftAction", page: "payroll", heavy: null },
  { name: "addEmployeePerkAction", page: "payroll", heavy: null },
  // Labor
  { name: "saveScheduleRulesAction", page: "labor", heavy: null },
  { name: "syncAdpAction", page: "labor", heavy: "adp-sync" },
  { name: "pollAdpSyncAction", page: "labor", heavy: null },
  { name: "runningAdpSyncAction", page: "labor", heavy: null },
  { name: "saveDraftsToAdpAction", page: "labor", heavy: "adp-schedule-write" },
  { name: "publishWeekAction", page: "labor", heavy: "adp-schedule-write" },
  { name: "schedulePushStatusAction", page: "labor", heavy: null },
  { name: "approveUnavailabilityAction", page: "labor", heavy: "adp-unavail-approve" },
  { name: "pollUnavailabilityApproveAction", page: "labor", heavy: null },
  { name: "decidePunchGapAction", page: "labor", heavy: null },
  { name: "acceptPunchGapsAction", page: "labor", heavy: null },
  { name: "dismissPunchGapsAction", page: "labor", heavy: null },
  { name: "writePunchGapsToAdpAction", page: "labor", heavy: "adp-punch-fix" },
  { name: "pollPunchGapWriteAction", page: "labor", heavy: null },
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
  // Monthly recognition (Issue #369)
  { name: "syncRecognitionSourcesAction", page: "automations", heavy: null },
  { name: "previewRecognitionAction", page: "automations", heavy: null },
  { name: "dmRecognitionDraftAction", page: "automations", heavy: null },
  { name: "approveRecognitionAction", page: "automations", heavy: "square-gift-cards" },
  { name: "sendTestGiftCardAction", page: "automations", heavy: "square-gift-cards" },
  { name: "reshapeRecognitionPostAction", page: "automations", heavy: null },
  { name: "markGiftCardsIssuedAction", page: "automations", heavy: null },
] as const;

export type MutatingActionName = (typeof MUTATING_ACTIONS)[number]["name"];
