# Issue #397 — Daily missing-punch check in Shift Coverage & Trades

Agent-internal plan. The operator approved the jam and the message format in chat on 2026-10-13/14. Per
`self-drive.mdc` § After alignment, this plan is not presented for review. Sources:
- `CONTRIBUTING.md`: dev loop and evidence.
- `.cursor/rules/bhaga-principles.mdc`: idempotent upserts, America/Chicago, breadcrumbs, and read-only
  ADP except flagged writes.
- `.cursor/rules/bhaga.mdc` invariant 6.
- `RUNBOOK.md` § Missing punches and § Unavailability reminder.
- `docs/FEATURE_FLAGS.md` `PUNCH_FIX_WRITEBACK`.

Evidence tier: unit-only
waiver: no sandbox scenario covers ClickUp chat or the ADP Timecards editor. Live evidence is the composed
    post sent from prod gap data to the operator's own ClickUp DM (`8cr6661-77`) for approval, plus the
    unit suites below. The ADP missing-in writer reuses the calibrated Edit / In Time / Save selectors.

## Agreed behaviour

- **The daily post.** One post at 10:00 CT (`automations` row `punch-check`, destination channel
  `8cr6661-1617`), and only when something is open. It covers open gaps from the last 14 days, excluding
  `in_progress` gaps and any gap whose latest decision is applied, applying, pending_write,
  already_resolved, not_in_hours or rejected. Each person gets an inline mention
  `[@Name](#user_mention#<clickupId>)`, matched by email: ADP profile email ↔ ClickUp member email.
- **Reply sweeps.** Staff reply in the post's thread, and BHAGA never replies.
  - Sweeps run every day at 21:00 CT and on Monday at 06:00 CT, before the 07:00 `bhaga-payroll-draft`.
  - A reply that is understood becomes a `punch_gap_decisions` row with `decided_by="clickup:<Name>"`.
    The webhook then starts the existing apply-only job, the same path as the console's Write to ADP.
  - Once every decision from a reply is applied, already_resolved, not_in_hours or rejected, the reply
    gets a ✅ reaction. Anything else stays open and shows up in the next day's post.
- **Monday 06:00.** Open missing-in and missing-out gaps (when a suggestion exists) are accepted at the
  scheduled time (`decided_by="auto:payroll-default"`). No-punch days are never auto-filled.
- **Missing in = missing out.** For a missing clock-in, `classify_day` suggests the scheduled start, and a
  new `apply_in_punch` fills the blank In Time.

## Milestone 1 — missing clock-in write path + email capture (Sonnet)

- `skills/bhaga_labor/punch_gaps.py:188` `classify_day`: MISSING_IN gets `suggested_in` from
  `suggest_open(out_time, scheduled_ranges)`. That is the start of the last scheduled range beginning
  before the clock-out, with `RULE_SCHEDULED_START = "open_at_scheduled_start"`, and hours = out − in.
- `skills/adp_run_automation/timecard_fix_backend.py`:
  - New `apply_in_punch(ui, *, date, entry_index, expect_out, in_time, comment) -> ApplyResult`. It
    follows the same rules as `apply_out_punch:109`: re-read, at most one Save, never retry, read-back
    verify.
  - `comment_text(..., missing_in=False, via="BHAGA Operator Console")`.
- `agents/bhaga/scripts/punch_fix_apply.py:250`: a `missing_in` branch (needs `in_time` and
  `out_time`). Plus `_comment_source(decided_by) -> (via, who)` for staff and auto decisions.
- `apps/operator-console/lib/labor/punch-gaps.ts:441` `writableToAdp`: add `missing_in`.
- `skills/adp_run_automation/pay_info_backend.py:624` `scrape_one_pay_info(..., want_email=True)`:
  - `extract_emails(text) -> list[str]` runs a regex over the pane text. If it finds nothing,
    `_read_personal_emails(page)` opens the Personal info tab and polls for up to 8 s.
  - The result goes into the `"emails"` key, best-effort with `[pay_info] BREADCRUMB email_not_found`.
  - `scrape_pay_info_rates` collects `contacts`, and `write_pay_info_json(contacts=)` writes them.
  - `write_employee_contacts_bq(payload)` MERGEs on (store, employee_id) and never blanks an address
    it already has. The loader calls it at `agents/bhaga/scripts/backfill_from_downloads.py:830`.
- `core/migrations/089_punch_check.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.employee_contacts` (
    store STRING NOT NULL, employee_id STRING NOT NULL, emails_json STRING,
    source STRING, scraped_at_utc TIMESTAMP);
  CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.punch_check_items` (
    store STRING NOT NULL, post_date_ct DATE NOT NULL, message_id STRING NOT NULL,
    channel_id STRING, date DATE NOT NULL, employee_id STRING NOT NULL, kind STRING,
    clickup_user_id STRING, posted_at TIMESTAMP);
  CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.punch_check_replies` (
    store STRING NOT NULL, reply_id STRING NOT NULL, message_id STRING, author_id STRING,
    employee_id STRING, text STRING, outcome STRING, detail STRING,
    decision_ids_json STRING, processed_at TIMESTAMP, reacted_at TIMESTAMP);
  ```

```bash
python3 -m pytest skills/bhaga_labor/test_punch_gaps.py skills/adp_run_automation/test_timecard_fix_backend.py agents/bhaga/scripts/test_punch_fix_apply.py skills/adp_run_automation/test_pay_info_backend.py -q
```
Pass criteria:
- Missing-in happy path: one Save and a read-back of (in, out).
- Already-filled In → already_resolved with no Save.
- Mismatched out → failed with no Save.
- In not before out → failed.
- Save raises → failed, never retried.
- The email regex picks addresses out of shadow-DOM text, and a blank scrape keeps the stored email.

## Milestone 2 — ClickUp helpers + reply parser (Sonnet)

- `skills/clickup_chat/runner.py`:
  - `fetch_replies(message_id, *, team_id, pat=None) -> list[dict]`
  - `add_reaction(message_id, reaction="white_check_mark", *, team_id, pat=None) -> bool`
  - `list_members(team_id, *, pat=None) -> list[dict]` via v2 `/team`
  - `mention(user_id, name) -> str`
- `skills/bhaga_labor/punch_reply.py` (pure):
  `parse_reply(text, items: list[ReplyItem]) -> list[Resolution]`.
  - Handles "out 3:20", "in 2:05", "2:15-8:45", a bare time (it fills the missing side), confirm words
    (use the scheduled time) and "didn't work/off" (rejects a no-punch day).
  - AM/PM is inferred as whichever reading is nearest the scheduled anchor. A date token ("mon",
    "10/12") narrows which items a reply targets.
  - Out must come after in, with a span of at most 16 h. Anything else resolves nothing.

```bash
python3 -m pytest skills/bhaga_labor/test_punch_reply.py skills/clickup_chat/ -q
```
Pass criteria:
- Every phrase in the table in `test_punch_reply.py` resolves as listed.
- Ambiguous cases resolve nothing: two items with one time, a contradiction, out before in.

## Milestone 3 — punch_check job + webhook tick (Sonnet)

- `agents/bhaga/scripts/punch_check.py`. Pure functions:
  - `compose(today, items, people, footer) -> str`, in the approved format, with the Sunday "payroll
    runs Monday morning" line.
  - `sweep_due(now) -> Optional[str]`, returning `"evening" | "payroll" | None`.

  IO functions:
  - `open_items`, `people_for`, `post_daily`, `sweep_replies(..., trigger_apply)`,
    `payroll_defaults`, `react_done`.
  - `run_tick(now=None, trigger_apply=None) -> dict`.
  - Flag `BHAGA_PUNCH_CHECK_APPLY=1` on `bhaga-webhook` claims the decisions and starts the job. When the
    flag is off, decisions stay `recorded` for the console's Write to ADP.
- `cloud/webhook/handler.py:1991` `_run_unavailability_reminder_job` also runs
  `punch_check.run_tick(trigger_apply=_apply_punch_decisions)`. That function calls
  `_trigger_cloud_run_job_with_env(date, [("BHAGA_PUNCH_FIX_APPLY_ONLY","1"),
  ("BHAGA_PUNCH_FIX_WRITEBACK","1"), ("BHAGA_PUNCH_FIX_DECISION_IDS", ids)])`.
- The webhook's busy guard may refuse to start the job. In that case the claim goes back to `recorded`
  with the reply `queued`, and the next 15-minute tick tries again. That repeats starting the job, never
  an ADP Save.

```bash
python3 -m pytest agents/bhaga/scripts/test_punch_check.py cloud/webhook/test_handler.py -q
BHAGA_DATASTORE=bigquery python3 -m agents.bhaga.scripts.punch_check --dry-run
```
Pass criteria:
- Golden compose matches the approved text, and the Sunday line is checked separately.
- Nothing open → no post. A second tick in the same day → no post.
- A reply → a decision plus the job trigger, with no bot message.
- An applied decision → ✅. A failed decision → no ✅, and the item is in the next post.
- Monday defaults cover missing in and out only.

## Invariants

These are preserved, and nothing here may break them:
- America/Chicago for every date and gate.
- Idempotent: one post per CT day through `automation_posts`, and replies processed once through the
  `punch_check_replies` MERGE on reply_id.
- An ADP Save is never retried, and the busy-guard retry only repeats starting the job.
- ADP stays read-only apart from the flagged punch write.
- Every failure leaves a breadcrumb: `BREADCRUMB punch_check`, `[punch-fix] FAIL`, `[pay_info] BREADCRUMB
  email_not_found`.
- Sandbox isolation through `core.datastore`.

**Feature-flag decision.** Can it silently produce wrong numbers? Yes. Staff free text and Monday
defaults write hours into ADP. The new flag `PUNCH_CHECK_APPLY` (`BHAGA_PUNCH_CHECK_APPLY=1` on
`bhaga-webhook`) is off by default and gets a row in `docs/FEATURE_FLAGS.md`. The post itself is
informational, and its on/off switch is the `automations` row. The change is additive: with the flag
off, existing console behaviour is unchanged. Out of scope: console UI for the new automation, and
operator-entered missing-in decisions in the console.

**Model routing (cost playbook):** M1–M3 on Sonnet, with Opus for the PR review. One chat per PR.

## Docs lock-step

- `RUNBOOK.md`: § Missing-punch check, scheduler reuse, and the flag flip command.
- `agents/bhaga/scripts/README.md`
- `.cursor/rules/bhaga.mdc` invariant 6 (staff channel post + flagged missing-in write)
- `docs/FEATURE_FLAGS.md`
- `PROGRESS.md`

Then run `python3 scripts/check_doc_freshness.py --base origin/main`.

## Branch / PR

- One branch, `cursor/397-want-to-work-on-new-shift-communication-feature-2-things-a898`, with PR #398
  (`--base main`) opened as bot `jarvis-agent-bot328`.
- Push with `--no-verify` only if the cost hook is unavailable on the VM.
- Reply to every comment, and never self-merge.
- Post-merge: apply migration 089, set `BHAGA_PUNCH_CHECK_APPLY=1` on `bhaga-webhook`, and confirm the
  next 10:00 CT tick posts once.
