# Issue #508 — "Last 90 days" Period + turn an assigned shift into an open shift

Branch `fix/i508-want-to-work-on-2-new` · jam agreed 2026-11-08 (90d Home goals Option A = monthly × 3;
shift → open Option A = new change kind, open shift first, then delete). Consulted: `CONTRIBUTING.md`
(dev loop, §4 evidence), `docs/contributing/ui-polish.md` (D1–D12), `.cursor/rules/bhaga.mdc` invariant 6
(schedule write is operator-clicked, flagged, never retried), `docs/plans/i434-shift-changes.md`
(Changes to ADP tray, `shift-reassign` pattern), `plan-execution-readiness.mdc`.

Evidence tier: sandbox-live
scenario: adp-apply-shift-to-open (prod ADP dry run + one live round trip on a Dolce shift: make open, then assign back)

Feature flag: no new flag — additive (existing kinds and presets unchanged). Rides `FEATURES.adpScheduleWrite` / `CONSOLE_ADP_SCHEDULE_WRITE` (already on).
"Can it silently produce wrong numbers?" — no: every row is confirmed by an ADP re-read and shown in the
tray; the 90d preset is a pure date window. No `FEATURE_FLAGS.md` entry beyond extending the existing row.

Invariants preserved: America/Chicago "today" (`chicagoToday`), shifts change from tomorrow on, never
retried (not in `RETRY_KINDS`), each shift published from its own pane (never Publish drafts), idempotent
re-read confirmation, no hand edits in ADP (B5).

## Milestone 1 — "Last 90 days" Period (Composer)

- `apps/operator-console/lib/filters/range.ts:8` `RangePreset` add `"90d"`; `RANGE_PRESETS:17` add
  `{ value: "90d", label: "Last 90 days" }` after 30d; `resolveRange` switch add
  `case "90d"` → `addDays(today, -89)`..today. `isMonthLike:93` include `"90d"`; new
  `export function goalMonths(preset: RangePreset): number` → `preset === "90d" ? 3 : 1`.
- `apps/operator-console/lib/kpi/health.ts:109` `periodGoal` multiplies the monthly value by
  `goalMonths(win.preset)`; `raw` stays the stored monthly goal (the inline editor edits that key).
  Info copy at `:259` reads "vs 3 × goal_net_sales_monthly" on 90d.
- Tests: `__tests__/range.test.ts` (90d window, label, `RANGE_PRESETS` order, `isMonthLike`, `goalMonths`).
- Test: `cd apps/operator-console && npx vitest run __tests__/range.test.ts` — pass = all green.

## Milestone 2 — `shift-to-open` change in the console (Sonnet)

- `apps/operator-console/lib/labor/adp-changes.ts:18` `ShiftChange` add
  `({ kind: "shift-to-open"; date: string; employee: string } & Times)`; `shiftKey` → `shift|…` group;
  `shiftText` → `` `${employee} · ${when} → Open shift` ``; `SHIFT_LABEL` → `"Make open"`; `shiftError`
  needs employee; `parseShift` rebuilds it; `pruneQueue` keeps it while `hasShift`.
- `apps/operator-console/components/labor/availability/EntrySheet.tsx:983-1016` assigned-shift drawer:
  `PersonSelect` gets an extra first item `OPEN_SHIFT = "__open__"` labelled "Open shift (unassigned)"
  (shadcn `SelectItem`, italic like the open-shift drawer title); choosing it stages
  `{ kind: "shift-to-open", employee, ...base }`; retimed + open = `[remove, open-add count 1 at new times]`.
  Hint copy: "ADP can't unassign a shift, so this adds an open shift at the same times, then deletes
  <name>'s." Footer unchanged (Delete · Save to changes), D12.
- `apps/operator-console/lib/labor/adp-change-store.ts` — generic, no change. `lib/actions/MUTATING_ACTIONS.md`
  row lists "make open".
- Tests: `__tests__/adp-changes.test.ts` (key, text, label, validate, prune), `__tests__/entry-sheet-shifts.test.tsx`
  (pick Open shift → staged change).
- Test: `cd apps/operator-console && npx vitest run __tests__/adp-changes.test.ts __tests__/entry-sheet-shifts.test.tsx && npx tsc --noEmit`.

## Milestone 3 — ADP job (Sonnet)

- `agents/bhaga/scripts/adp_schedule_write.py:533` `APPLY_ORDER["shift-to-open"] = 2`; `_NEEDS_EMPLOYEE:554` add it.
- New `def _make_open(frame, page, row: dict, open_now: Counter, *, dry_run: bool) -> Counter` next to
  `_apply_open:649`: shift missing → `ScheduleWriteError("the shift is no longer in ADP")`;
  `wb.create_open_shift(..., publish=True)`; re-read `wb.read_open_shifts` and require one more open shift
  at the slot, else `UnconfirmedSave("… <name>'s shift was not deleted")`; then `wb.delete_any_shift`;
  a failed delete → `ScheduleWriteError("added the open shift but could not delete <name>'s shift … — delete it in ADP")`.
  Dry run walks create (no save) + delete (no confirm).
- `run_apply.walk:821` routes `shift-to-open` to `_make_open`; failure re-read of `open_now` covers it.
- Tests: `agents/bhaga/scripts/test_adp_schedule_write.py` — happy path order (create before delete),
  open shift not confirmed → no delete, delete fails → failed row with the breadcrumb text, shift gone → failed,
  parse_changes accepts `shift-to-open … employee=`.
- Test: `python3 -m pytest -q agents/bhaga/scripts/test_adp_schedule_write.py`.

## Milestone 4 — Evidence + docs (Sonnet)

- Localhost screenshots (`apps/operator-console/scripts/capture_evidence.py`): Labor Period on 90d; Home on
  90d; shift drawer with "Open shift (unassigned)" + tray line, desktop + 390px.
- Prod ADP dry run: `python3 -m agents.bhaga.scripts.adp_schedule_write apply --changes "shift-to-open <date> <HH:MM-HH:MM> employee=Johnson,_Dolce_J" --dry-run` (local CDP attach, OTP via BHAGA Slack DM).
- Live round trip on Dolce's shift: queue + apply `shift-to-open`, then `open-assign` back to Dolce; ADP re-read after each.
- Docs lock-step: `RUNBOOK.md` (Changes to ADP failure texts), `agents/bhaga/scripts/README.md:173`,
  `.cursor/rules/bhaga.mdc` invariant 6 list, `docs/FEATURE_FLAGS.md` schedule-write row,
  `docs/operator-console/ARCHITECTURE.md`; `python3 scripts/check_doc_freshness.py --base origin/main`.
  PROGRESS via retro follow-up.

Per-scenario evidence: E1 90d window + goal ×3; E2 30d/7d/week goals unchanged; E3 drawer stages one
"Make open" line; E4 server rejects past/unknown; E5 create-then-delete order, no delete when the open
shift isn't confirmed, named failure when delete fails; E6 prod dry run; E7 live round trip, final = start.

UX (D1–D12): enhances the existing shift drawer (D1), same Person control as reassign (D2, D6), one tray
line per intent (D4), plain wording "Unassign" / "Open shift (unassigned)" (D8), iPhone 390px checked (D3).

Branch/PR: one branch one PR, `--base main`, bot `jarvis-agent-bot328`, `--no-verify` push only if hook
blocks on cost, never self-merge, reply to every thread. Model routing: Composer M1, Sonnet M2–M4; one chat.
