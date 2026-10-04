# Operator Console — mutating actions audit

Issue #175. Every control that writes state or kicks off side work must use
`useConsoleAction` and return `ActionAck`. Heavy follow-ups enqueue Cloud Run
Jobs (Option B); they must never block the click path via daemon threads.

| Page | Control | Server symbol | Heavy follow-up |
|---|---|---|---|
| Home | Goals drawer | `saveGoalsAction` | — |
| Home | Inline goal pencil | `saveGoalAction` | — |
| Labor | Weekly hours goal pencil | `saveGoalAction` (`goal_labor_hours_week`) | — |
| Inventory | Restock submit | `submitRestockAction` | `order-reco` (`sp_refresh_order_reco` CALL, not awaited) |
| Inventory | Replace estimated date | `replaceEstimatedRestockDateAction` | `order-reco` |
| Inventory | Capacity | `setCapacityAction` | `order-reco` |
| Inventory | Order-reco status poll | `orderRecoStatusAction` | — (read-only; `OrderRecoStatusProvider`) |
| Inventory | Order-reco Retry / Update now | `retryOrderRecoAction` | `order-reco` |
| Inventory | Page self-heal | `ensureOrderRecoFresh` (RSC, `lib/bhaga/orderReco.ts`) | `order-reco` only when the live inputs fingerprint ≠ last committed run; never auto-retries a failed run |
| Payroll | Tip exemptions Update | `applyTipExemptionsAction` | `model-recompute` job |
| Payroll | Run ADP Preview | `runPayrollDraftAction` / `pollPayrollDraftAction` | Headless `adp-payroll-draft` (local laptop when BYPASS_IAP); BQ stores Preview hours + Gross |
| Payroll | Recognition bonus | `addRecognitionBonusAction` | — |
| Payroll | Training quick-add (flag off) | `addTrainingShiftAction` | — |
| Payroll | Add reimbursement | `addEmployeePerkAction` | — |
| Payroll / Labor | Sync clocked hours | `syncClockedHoursAction` / `pollClockedHoursSyncAction` | Headless `adp-timecard` (local laptop when BYPASS_IAP); Timecard + BQ shifts/punches, not pay_info |
| Labor | Punches Accept / Edit / Dismiss | `decidePunchGapAction` | — (appends `punch_gap_decisions`; nothing reaches ADP) |
| Labor | Punches bulk Accept / Dismiss (selected rows) | `acceptPunchGapsAction`, `dismissPunchGapsAction` | — (one decision per selected row; nothing reaches ADP) |
| Labor | Punches Write to ADP | `writePunchGapsToAdpAction` / `pollPunchGapWriteAction` | Headless `adp-punch-fix` on Cloud Run (`bhaga-daily-refresh`; local dev needs `BHAGA_ADP_PREVIEW_JOB`): fills approved Out Times / adds missing entries in ADP Timecards, then timecard resync. Flag `punchFixWriteback` |
| Labor | Sync scheduled shifts | `syncScheduledShiftsAction` / `pollScheduledShiftsSyncAction` | Headless `adp-schedule` |
| Accounting | Link / Relink | `createPlaidLinkTokenAction`, `exchangePlaidPublicTokenAction` | Plaid sync (in-request, staged UX) |
| Accounting | Sync now | `syncPlaidNowAction` | Plaid sync |
| Accounting | Overrides / taxonomy / rules | `setTxnCategoryOverrideAction`, `upsertTaxonomyNodeAction`, `setTaxonomyNodeEnabledAction`, `setCategoryRuleEnabledAction`, `setTaxonomyExcludeAction`, `dryRunRuleAction`, `previewRuleMatchesAction`, `commitRuleFromTxnAction`, `revertRuleEvidenceAction`, `reapplyPlaidCategoriesAction`, `setPlaidInternalAction` | — |
| Automations | Team pulse save / preview / post once | `saveTeamPulseConfigAction`, `previewTeamPulseAction`, `postTeamPulseOnceAction` | — |

Canonical machine-readable list: [`registry.ts`](./registry.ts).
Gate: `python3 scripts/check_operator_console_actions.py`.

## Live evidence (PR #193)

- Console review-deploy: `operator-console-00068-bkf`, minScale=1
- Job: `bhaga-daily-refresh-dbr7z` logged `[order-reco-only] store=palmetto — skipping scrape/model` exit 0
