# Operator Console — mutating actions audit

Issue #175. Every control that writes state or kicks off side work must use
`useConsoleAction` and return `ActionAck`. Heavy follow-ups enqueue Cloud Run
Jobs (Option B); they must never block the click path via daemon threads.

| Page | Control | Server symbol | Heavy follow-up |
|---|---|---|---|
| Home | Goals drawer | `saveGoalsAction` | — |
| Home | Inline goal pencil | `saveGoalAction` | — |
| Inventory | Restock submit | `submitRestockAction` | `order-reco` job |
| Inventory | Replace estimated date | `replaceEstimatedRestockDateAction` | `order-reco` job |
| Inventory | Capacity | `setCapacityAction` | `order-reco` job |
| Inventory | Page self-heal | `ensureOrderRecoFresh` (RSC) | `order-reco` job when stale |
| Payroll | Tip exemptions Update | `applyTipExemptionsAction` | `model-recompute` job |
| Payroll | ADP Preview (delete after) | `runPayrollDraftAction` / `pollPayrollDraftAction` | `adp-payroll-draft` job |
| Payroll | Recognition bonus | `addRecognitionBonusAction` | — |
| Payroll | Training quick-add (flag off) | `addTrainingShiftAction` | — |
| Accounting | Link / Relink | `createPlaidLinkTokenAction`, `exchangePlaidPublicTokenAction` | Plaid sync (in-request, staged UX) |
| Accounting | Sync now | `syncPlaidNowAction` | Plaid sync |
| Accounting | Overrides / taxonomy / rules | `setTxnCategoryOverrideAction`, `upsertTaxonomyNodeAction`, `setTaxonomyNodeEnabledAction`, `setCategoryRuleEnabledAction`, `setTaxonomyExcludeAction`, `dryRunRuleAction`, `previewRuleMatchesAction`, `commitRuleFromTxnAction`, `revertRuleEvidenceAction`, `reapplyPlaidCategoriesAction`, `setPlaidInternalAction` | — |
| Automations | Team pulse save / preview / post once | `saveTeamPulseConfigAction`, `previewTeamPulseAction`, `postTeamPulseOnceAction` | — |

Canonical machine-readable list: [`registry.ts`](./registry.ts).
Gate: `python3 scripts/check_operator_console_actions.py`.

## Live evidence (PR #193)

- Console review-deploy: `operator-console-00068-bkf`, minScale=1
- Job: `bhaga-daily-refresh-dbr7z` logged `[order-reco-only] store=palmetto — skipping scrape/model` exit 0
