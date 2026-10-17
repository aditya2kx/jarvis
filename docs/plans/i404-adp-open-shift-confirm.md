# Issue #404 — Save to ADP drafts never fails on open shifts

Operator 2026-10-17 00:43 ET: "I want this flow resilient, robust and accurate. it should never fail" (jam recorded via `phase_state.py advance --operator-approved`).

Evidence tier: unit-only
waiver: The only end-to-end path writes real ADP drafts for a real push. Every new ADP interaction was exercised live against prod ADP on 2026-10-17 (`/tmp/live_verify.py`, output in PR §4: open-shift read, assigned-draft lookup hit/miss, `create_open_shift` confirmed 26 → 27 without pressing Publish). The full run is proved post-merge by the operator's Save for week 2026-10-26.

## Root cause

`bhaga-daily-refresh-8v894` (2026-10-17 04:37Z, push `be78d867…`) drafted 16 assigned shifts, then `create_open_shift` (2026-10-31 1:30–8:30 PM) raised `UnconfirmedSave`. Live probe 2026-10-17: after **Save draft** ADP replaces the wizard with an `open-shift-dialog` pane — "An open shift with 1 shift has been created and added to the schedule as a draft shift … Edit | Publish" — which hides the toolbar's "Publish drafts (N)". `skills/adp_run_automation/schedule_write_backend.py` `create_open_shift` (line ~316) went straight to `_wait_drafts`, which never read the count; `recheck` and `refresh_schedule` then ran under the same pane ("Publish drafts button not found", open-shift scrape intercepted). After `_back_out` (header Back) the count read 26: the save had worked. Same failure on 2026-10-16 (`n8mp2`, Oct 27). No open shift had ever been confirmed by the job.

## Invariant

Only `Save draft` / `Save as draft` are pressed; the created pane's **Publish** never is (`_back_out` presses exact `Close`, `Back` or Escape). Never two ADP drafts for one row: adoption compares ADP's open shifts per (date, start, end) with BQ `drafted`/`published` open rows. Statuses still flush before the week re-read; no row left `queued`. America/Chicago dates, BQ upserts unchanged.

Feature flag: no new flag — writes stay behind `FEATURES.adpScheduleWrite` and the operator click; this cannot silently produce wrong numbers (it changes confirmation and de-duplication of drafts only). Out of scope: open-shift eligibility (ADP's default listed two employees), pay_info speed (separate ADP report spike).

## Milestone 1 — confirm open-shift saves (Composer)

`schedule_write_backend.py`: `OPEN_SHIFT_CREATED_RE`, `_pane_text(frame)`; `create_open_shift` waits ≤15 s for the created pane, `_back_out`, then `_wait_drafts(before + 1)`; the created pane confirms a lagging count. New read-only helpers: `parse_clock(text) -> int`, `open_shift_counts(cells, week_start) -> Counter`, `read_open_shifts(page, frame, week_start) -> Counter`, `has_draft_shift(frame, page, *, date_iso, employee, start_min, end_min) -> bool`.

**Verify:** `python3 -m pytest -q skills/adp_run_automation/test_schedule_write_backend.py` (CreateOpenShiftConfirm fails 2/3 on the old `create_open_shift`).

## Milestone 2 — adopt, don't duplicate; resolve unconfirmed saves by the exact shift (Composer)

`agents/bhaga/scripts/adp_schedule_write.py` `run_drafts`: `_known_open_slots(store, week)`; per week with open rows, `unclaimed = ADP open − known`, an open row in `unclaimed` → `drafted` with no save. `UnconfirmedSave` → `reopen` + `in_adp` (assigned: `has_draft_shift`; open: count above the week-start read) → `drafted` / `failed` and continue; only an unreadable look stops the run. An assigned save ADP rejects is adopted when `has_draft_shift`. `expected` re-read after every save (`count_now`). `refresh_schedule` backs out panes first.

**Verify:** `python3 -m pytest -q agents/bhaga/scripts/test_adp_schedule_write.py`

## Milestone 3 — docs (Composer)

`RUNBOOK.md` schedule-write paragraph, `agents/bhaga/scripts/README.md` `adp_schedule_write` row, `PROGRESS.md`.

**Verify:** `python3 scripts/check_doc_freshness.py`

## Scenarios

Happy: assigned + open shifts all save, each confirmed. Created pane with lagging count → drafted. Unconfirmed, ADP shows the shift → drafted, run continues (8v894 replay). Unconfirmed, ADP lacks it → failed, run continues. Look itself fails → stop, nothing left `queued` (n8mp2 replay). Retry of week 2026-10-26 → the three Oct 31 open shifts already in ADP adopted, Nov 1 rows created. Assigned already in ADP → adopted. Crash → every unsaved row failed, re-raised. Dry run → no ADP save, no BQ write.

## PR mechanics

Branch `fix/adp-open-shift-confirm`, one PR `--base main`, `Closes #404`, bot `jarvis-agent-bot328`, never self-merge, reply to every thread, babysit per `pr-workflow.mdc`. Post-merge: operator Sync ADP then Save for week 2026-10-26; expect 9 rows `drafted` and ADP's draft count to match.

Model routing: Composer for all milestones; no Opus plan review.
