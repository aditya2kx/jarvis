/**
 * Canonical inventory of every mutating server action in the operator console.
 * scripts/check_operator_console_actions.py fails if an exported *Action
 * under apps/operator-console/app/.../actions.ts is missing from this list
 * (future-proof gate).
 *
 * `access` lists the page (`"labor"`) or page sections (`"labor:coverage"`) whose
 * holders may run the action — any one is enough. Each action checks it server-side
 * with `actionDenied()` (lib/access/server.ts) before doing anything.
 */
export const MUTATING_ACTIONS = [
  // Home
  { name: "saveGoalsAction", page: "home", heavy: null, access: ["home:goals"] },
  { name: "saveGoalAction", page: "home", heavy: null, access: ["home:goals", "labor:labor-hours"] },
  // Inventory
  { name: "submitRestockAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "replaceEstimatedRestockDateAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "moveRestockDateAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "removeRestockDateAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "setCapacityAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "setRestockCadenceAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "setUsageDayOverrideAction", page: "inventory", heavy: "order-reco", access: ["inventory:usage-by-day"] },
  { name: "clearUsageDayOverrideAction", page: "inventory", heavy: "order-reco", access: ["inventory:usage-by-day"] },
  { name: "applyUsageDayOverridesAction", page: "inventory", heavy: "order-reco", access: ["inventory:usage-by-day"] },
  { name: "applyOrderTubOverridesAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "setCurrentQtyOverrideAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "clearCurrentQtyOverrideAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "applyCurrentQtyOverridesAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "clearCurrentQtyOverridesAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  { name: "orderRecoStatusAction", page: "inventory", heavy: null, access: ["inventory"] },
  { name: "retryOrderRecoAction", page: "inventory", heavy: "order-reco", access: ["inventory:order-reco"] },
  // Payroll
  { name: "addTrainingShiftAction", page: "payroll", heavy: null, access: ["payroll:tip-exemptions"] },
  { name: "addRecognitionBonusAction", page: "payroll", heavy: null, access: ["payroll:recognition"] },
  { name: "applyTipExemptionsAction", page: "payroll", heavy: "model-recompute", access: ["payroll:tip-exemptions"] },
  { name: "runPayrollDraftAction", page: "payroll", heavy: "adp-payroll-draft", access: ["payroll:adp-draft"] },
  { name: "pollPayrollDraftAction", page: "payroll", heavy: null, access: ["payroll:adp-draft"] },
  { name: "addEmployeePerkAction", page: "payroll", heavy: null, access: ["payroll:perks"] },
  // Labor
  { name: "saveScheduleRulesAction", page: "labor", heavy: null, access: ["labor:coverage"] },
  { name: "syncAdpAction", page: "labor", heavy: "adp-sync", access: ["labor", "payroll"] },
  { name: "pollAdpSyncAction", page: "labor", heavy: null, access: ["labor", "payroll"] },
  { name: "runningAdpSyncAction", page: "labor", heavy: null, access: ["labor", "payroll"] },
  { name: "saveDraftsToAdpAction", page: "labor", heavy: "adp-schedule-write", access: ["labor:coverage"] },
  { name: "publishWeekAction", page: "labor", heavy: "adp-schedule-write", access: ["labor:coverage"] },
  { name: "sendPublishMessageAction", page: "labor", heavy: "adp-schedule-write", access: ["labor:coverage"] },
  { name: "schedulePushStatusAction", page: "labor", heavy: null, access: ["labor:coverage"] },
  { name: "applyAdpChangesAction", page: "labor", heavy: "adp-schedule-write", access: ["labor:coverage", "labor:availability"] },
  { name: "adpChangesStatusAction", page: "labor", heavy: null, access: ["labor:coverage", "labor:availability"] },
  { name: "approveUnavailabilityAction", page: "labor", heavy: "adp-unavail-approve", access: ["labor:availability"] },
  { name: "pollUnavailabilityApproveAction", page: "labor", heavy: null, access: ["labor:availability"] },
  { name: "decidePunchGapAction", page: "labor", heavy: null, access: ["labor:punch-gaps"] },
  { name: "acceptPunchGapsAction", page: "labor", heavy: null, access: ["labor:punch-gaps"] },
  { name: "dismissPunchGapsAction", page: "labor", heavy: null, access: ["labor:punch-gaps"] },
  { name: "writePunchGapsToAdpAction", page: "labor", heavy: "adp-punch-fix", access: ["labor:punch-gaps"] },
  { name: "pollPunchGapWriteAction", page: "labor", heavy: null, access: ["labor:punch-gaps"] },
  // Accounting
  { name: "createPlaidLinkTokenAction", page: "accounting", heavy: null, access: ["accounting:bank-link"] },
  { name: "exchangePlaidPublicTokenAction", page: "accounting", heavy: "plaid-sync", access: ["accounting:bank-link"] },
  { name: "syncPlaidNowAction", page: "accounting", heavy: "plaid-sync", access: ["accounting:bank-link"] },
  { name: "setPlaidInternalAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "reapplyPlaidCategoriesAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "setTxnCategoryOverrideAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "upsertTaxonomyNodeAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "setTaxonomyNodeEnabledAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "setCategoryRuleEnabledAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "dryRunRuleAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "previewRuleMatchesAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "commitRuleFromTxnAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "revertRuleEvidenceAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  { name: "setTaxonomyExcludeAction", page: "accounting", heavy: null, access: ["accounting:ledger"] },
  // Automations (Issue #216)
  { name: "saveTeamPulseConfigAction", page: "automations", heavy: null, access: ["automations:team-pulse"] },
  { name: "previewTeamPulseAction", page: "automations", heavy: null, access: ["automations:team-pulse"] },
  { name: "postTeamPulseOnceAction", page: "automations", heavy: null, access: ["automations:team-pulse"] },
  // Unavailability reminder (Issue #381)
  { name: "saveReminderConfigAction", page: "automations", heavy: null, access: ["automations:unavailability-reminder"] },
  { name: "previewReminderAction", page: "automations", heavy: null, access: ["automations:unavailability-reminder"] },
  { name: "sendReminderOnceAction", page: "automations", heavy: null, access: ["automations:unavailability-reminder"] },
  // Monthly recognition (Issue #369)
  { name: "syncRecognitionSourcesAction", page: "automations", heavy: null, access: ["automations:monthly-recognition"] },
  { name: "previewRecognitionAction", page: "automations", heavy: null, access: ["automations:monthly-recognition"] },
  { name: "dmRecognitionDraftAction", page: "automations", heavy: null, access: ["automations:monthly-recognition"] },
  { name: "approveRecognitionAction", page: "automations", heavy: "square-gift-cards", access: ["automations:monthly-recognition"] },
  { name: "sendTestGiftCardAction", page: "automations", heavy: "square-gift-cards", access: ["automations:monthly-recognition"] },
  { name: "reshapeRecognitionPostAction", page: "automations", heavy: null, access: ["automations:monthly-recognition"] },
  { name: "markGiftCardsIssuedAction", page: "automations", heavy: null, access: ["automations:monthly-recognition"] },
] as const;

export type MutatingActionName = (typeof MUTATING_ACTIONS)[number]["name"];
