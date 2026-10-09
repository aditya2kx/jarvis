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
| Payroll / Labor | Sync ADP | `syncAdpAction` / `pollAdpSyncAction` / `runningAdpSyncAction` (resumes the status after a reload) | Headless `BHAGA_ADP_SYNC_ALL` on Cloud Run (`bhaga-daily-refresh`): Timecard, Team Schedule (assigned + open), earnings, liability, pay rates in one login — never a payroll draft |
| Labor | Save scheduling rules | `saveScheduleRulesAction` | Appends a `labor_schedule_rules` version (history kept) |
| Labor | Save to ADP as drafts / Publish week | `saveDraftsToAdpAction`, `publishWeekAction` / `schedulePushStatusAction` | Behind `FEATURES.adpScheduleWrite` (`CONSOLE_ADP_SCHEDULE_WRITE=1`). Queues `labor_schedule_pushes` rows, then `BHAGA_ADP_SCHEDULE_WRITE` on Cloud Run creates ADP draft shifts; publish clicks ADP Publish drafts, re-reads ADP from tomorrow through that week, then DMs the operator a draft Shift Coverage post listing every open shift left in that window (nothing posted to the team) |
| Labor | Approve unavailability request | `approveUnavailabilityAction` / `pollUnavailabilityApproveAction` | Behind `FEATURES.adpUnavailabilityApprove` (`CONSOLE_ADP_UNAVAIL_APPROVE=1`). `BHAGA_ADP_UNAVAIL_APPROVE` on Cloud Run clicks APPROVE on the one matching card in Team Schedule › Pending requests (never Reject, never retried), then the schedule-only refresh |
| Labor | Punches Accept / Edit / Dismiss | `decidePunchGapAction` | — (appends `punch_gap_decisions`; nothing reaches ADP) |
| Labor | Punches bulk Accept / Dismiss (selected rows) | `acceptPunchGapsAction`, `dismissPunchGapsAction` | — (one decision per selected row; nothing reaches ADP) |
| Labor | Punches Write to ADP | `writePunchGapsToAdpAction` / `pollPunchGapWriteAction` | Headless `adp-punch-fix` on Cloud Run (`bhaga-daily-refresh`; local dev needs `BHAGA_ADP_PREVIEW_JOB`): fills approved Out Times / adds missing entries in ADP Timecards, re-downloads the Timecard export until each fix is in it (else `not_in_hours`, Issue #358), then timecard resync. Flag `punchFixWriteback` |
| Accounting | Link / Relink | `createPlaidLinkTokenAction`, `exchangePlaidPublicTokenAction` | Plaid sync (in-request, staged UX) |
| Accounting | Sync now | `syncPlaidNowAction` | Plaid sync |
| Accounting | Overrides / taxonomy / rules | `setTxnCategoryOverrideAction`, `upsertTaxonomyNodeAction`, `setTaxonomyNodeEnabledAction`, `setCategoryRuleEnabledAction`, `setTaxonomyExcludeAction`, `dryRunRuleAction`, `previewRuleMatchesAction`, `commitRuleFromTxnAction`, `revertRuleEvidenceAction`, `reapplyPlaidCategoriesAction`, `setPlaidInternalAction` | — |
| Automations | Team pulse save / preview / post once | `saveTeamPulseConfigAction`, `previewTeamPulseAction`, `postTeamPulseOnceAction` | — |
| Automations | Unavailability reminder save / preview / DM me now | `saveReminderConfigAction`, `previewReminderAction`, `sendReminderOnceAction` | — (DMs the operator a draft to forward; the scheduled tick is `/unavailability-reminder` on `bhaga-webhook`, once per CT day) |
| Automations | Monthly recognition sync / Gemini draft / reshape by prompt / DM draft / mark issued by hand | `syncRecognitionSourcesAction`, `previewRecognitionAction`, `reshapeRecognitionPostAction`, `dmRecognitionDraftAction`, `markGiftCardsIssuedAction` | — (sync copies ClickUp chat into BQ `clickup_chat_messages`; the draft is DM'd to the operator to forward — nothing posts to the team channel; "mark issued" writes `recognition_gift_cards` rows with status `external`, no Square call) |
| Automations | Monthly recognition Approve & issue / $1 test card | `approveRecognitionAction`, `sendTestGiftCardAction` | Behind `FEATURES.recognitionGiftCards` (`CONSOLE_RECOGNITION_GIFT_CARDS=1`). Square create + ACTIVATE + read-back per card (`recognition_gift_cards` ledger, idempotency key per card), one Gmail per recipient, recap ClickUp DM. Failed rows are never auto-retried — "Retry failed" resumes from the recorded state |

Canonical machine-readable list: [`registry.ts`](./registry.ts).
Gate: `python3 scripts/check_operator_console_actions.py`.

## Live evidence (PR #193)

- Console review-deploy: `operator-console-00068-bkf`, minScale=1
- Job: `bhaga-daily-refresh-dbr7z` logged `[order-reco-only] store=palmetto — skipping scrape/model` exit 0
