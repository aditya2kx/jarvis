# Issue #381 — Shift Coverage publish DM + unavailability reminder

Agent-internal plan (jam + §4 approved in chat 2026-10-09; per `self-drive.mdc` § After alignment
it is not presented for review). Sources: `CONTRIBUTING.md` (dev loop / evidence), 
`.cursor/rules/bhaga-principles.mdc`, `.cursor/rules/bhaga.mdc` invariant 6 (schedule write is
operator-clicked; only DMs to the operator), `RUNBOOK.md` § Team pulse.

Evidence tier: unit-only
waiver: no sandbox scenario exercises ClickUp DMs; live evidence is real DMs to the operator's own
ClickUp DM (the actual destination — nothing team-visible) plus localhost console screenshots.

## Milestone 1 — publish DM (Sonnet)

- `agents/bhaga/scripts/adp_schedule_write.py:116` `refresh_schedule(page, store, week, *, weeks=1)`
  — scrape `weeks` weeks instead of 1.
- new `refresh_open_shift_window(page, store, week_start) -> bool` — `goto_week` to
  `week_start_of(tomorrow CT)` and refresh through `week_start`; breadcrumb on failure.
- `run_publish` (line ~395): both publish-confirmed branches call `refresh_open_shift_window`; pass
  `fresh=` to `notify_published`.
- `open_shift_window(week_start, today) -> (today+1, week_start+6)`; `open_shifts_between(lo, hi)`
  reads `adp_open_shifts` by `date` (not `week_start`) + latest `scraped_at_utc`.
- `publish_message(week_start, open_shifts, *, stale_as_of=None)` — operator's 2026-10-09 format:
  `@everyone 🗞️` intro, "Additionally…" paragraph only when shifts exist, `1. Oct 13 (Tuesday)` /
  `    1. 3:00 PM → 8:30 PM`.
- CLI `--publish-dm --week-start <Mon>` re-sends the DM from BQ (demo / ops).

```bash
python3 -m pytest agents/bhaga/scripts/test_adp_schedule_write.py -q
```
Pass: golden equality with the operator post; empty window omits the paragraph; window = tomorrow →
published Sunday; 3-week refresh from Fri, 2-week from Sun; DM failure → `BREADCRUMB adp_publish_dm`.

## Milestone 2 — reminder job + webhook (Sonnet)

- `core/migrations/088_automations_followup_template.sql`:
  `ALTER TABLE ...automations ADD COLUMN IF NOT EXISTS followup_template STRING;`
- `agents/bhaga/scripts/unavailability_reminder.py`: `compose(today, days, template, followup)`,
  `is_due(now, *, days, hour, minute, enabled)`, `run_reminder(...)`; reuses `team_pulse`
  `already_posted` / `record_post(automation_id=)` / `resolve_target_channel`.
- `cloud/webhook/handler.py` `POST /unavailability-reminder` (team-pulse token) → background job.
- Fixture `core/testdata/unavailability_reminder_golden.json` shared with the console.

```bash
python3 -m pytest agents/bhaga/scripts/test_unavailability_reminder.py cloud/webhook/test_handler.py -q
```
Pass: golden Wed/Thu/month-boundary; off-day, before-time, disabled, second tick → no post.

## Milestone 3 — console card + editor (Sonnet)

- `apps/operator-console/lib/automations/unavailabilityReminder.ts` mirrors `compose`.
- `app/automations/unavailability-reminder/{page,ReminderEditor,actions}.tsx|ts` — Team-pulse
  primitives (`Card`, `Badge`, `Button`, `Input`, `Label`, `DataTable`, `useConsoleAction`); 44px
  tap targets, focus rings, disabled-while-pending; registry + `MUTATING_ACTIONS.md` rows.
- `lib/bq/writes.ts` `upsertAutomation` writes `followup_template` only when provided.

```bash
cd apps/operator-console && npx vitest run __tests__/unavailability-reminder.test.ts && npx tsc --noEmit -p .
python3 scripts/check_operator_console_actions.py
```

## Invariants

America/Chicago for every date (`_today_ct`, `ZoneInfo(tz)`); idempotent — one reminder per CT day
via `automation_posts`; publish DM still best-effort and posted once; ADP stays read-only apart from
the existing operator-clicked publish (the window refresh is a read).

Feature-flag decision: no new flag — nothing here can produce wrong numbers (output is a draft DM
the operator reads before forwarding); the publish path stays behind the existing
`FEATURES.adpScheduleWrite`, and the reminder is additive with its own on/off in the console.

Model routing (cost playbook): M1–M3 on Sonnet; Opus only for the PR review.

## Docs lock-step

`RUNBOOK.md` (publish paragraph + § Unavailability reminder), `agents/bhaga/scripts/README.md`,
`.cursor/rules/bhaga.mdc` invariant 6, `MUTATING_ACTIONS.md`; dev-flow: `self-drive.mdc`,
`docs/WORKFLOW.md`, `scripts/start_pr_session.py`, `user-preferences.mdc` #33/#34.
`python3 scripts/check_doc_freshness.py --base origin/main`.

## Branch / PR

One branch `fix/i381-work-when-i-publish-shifts-on`, PR `--base main` as `jarvis-agent-bot328`,
reply to every comment, never self-merge. Post-merge: create `bhaga-unavailability-reminder`
scheduler (RUNBOOK command), confirm Wed Oct 14 10:00 CT tick sends one DM.
