# Issue #406 — The console never shows a shift as failed when it is in ADP

Operator 2026-10-17 15:58 ET: "I still see three shifts failed … we should have thirty three … this flow needs to be resilient and robust." (jam recorded via `phase_state.py advance --operator-approved`).

Evidence tier: unit-only
waiver: The change only re-labels BQ rows from a schedule read. It was previewed read-only against live ADP + prod BQ (UPDATEs printed, not run); the real flip happens on the first schedule load after deploy and is checked in BQ and on the console card.

## Root cause

Push `be78d867…` left rows `palmetto|2026-10-31|OPEN|{810,900,960}|1230` `failed` (two `UnconfirmedSave` stops before #405). All three are drafts in ADP. The 17:08Z Sync ADP read them (`adp_open_shifts`), so `splitAgainstAdp` held them out of push `a237a9e4…` (`inAdp`). No push ever touched their rows again, and `reconcile_published` (`agents/bhaga/scripts/adp_schedule_write.py` line ~104) only looks at `drafted` rows. The card counted them as `3 failed`, with 30 drafts instead of 33.

## Invariant

ADP is never written. Only `labor_schedule_pushes.status`/`error`/`updated_at` of the **latest** row per row_key (matched by `push_id|row_key`) changes, and only on an exact match: same date, start and end, and the same person (`_same_person`) or open ↔ open. No assigned ↔ open cross-match for failed rows. Idempotent, so a second load finds nothing.

Feature flag: no new flag. This cannot silently produce wrong numbers: it changes only which status a row shows, and only to agree with ADP. Out of scope: the "Show Unavailability" toggle timeout seen in the last three schedule reads.

## Milestone 1 — reconcile failed rows (Composer)

`found_failed_rows(rows, states) -> dict[str, str]`; `reconcile_published(store, states)` selects `push_id` too, then UPDATEs by `CONCAT(push_id, '|', row_key)` per status and prints `failed_found_in_adp=N`. Called from `backfill_from_downloads._reconcile_published` on every schedule load (unchanged hook).

**Verify:** `python3 -m pytest -q agents/bhaga/scripts/test_adp_schedule_write.py`

## Milestone 2 — docs (Composer)

`RUNBOOK.md` publish/reconcile paragraph, `agents/bhaga/scripts/README.md`, `PROGRESS.md`.

**Verify:** `python3 scripts/check_doc_freshness.py`

## Scenarios

Failed + ADP draft → `drafted`. Failed + ADP published → `published`. Same times, different person / assigned vs open / other day → stays `failed`. Older pushes' rows for the same key are untouched. Legacy drafted → published unchanged.

## PR mechanics

Branch `fix/adp-reconcile-failed-drafts`, one PR `--base main`, `Closes #406`, bot `jarvis-agent-bot328`, never self-merge, reply to every thread. Post-merge: one schedule load (Sync ADP), then BQ for week 2026-10-26 shows 33 `drafted` and the card shows 33 ADP drafts, 0 failed.

Model routing: Composer; no Opus plan review.
