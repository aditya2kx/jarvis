# Issue #416 — Restock Friday horizon + jarvis-new-task Agents Window attach

Evidence tier: sandbox-e2e
scenario: inventory-restock-horizon

Jam / §4 approved in chat 2026-10-20 (labels via `phase_state.py advance --operator-approved`).

## Jam / §4 (approved)

1. **Inventory:** always keep ≥2 live restock dates on `/inventory` first table by auto-registering Fridays (`store_config restock_delivery_weekday=friday`, `restock_horizon_dates=2`). Top-up only; moved / off-Friday deliveries replace their Friday (slot model, 4e); register-only; never delete/move/overwrite Actuals or pins.
2. **jarvis-new-task:** when already in Agents Window (`CURSOR_LAYOUT=glass` + `CURSOR_AGENT=1`), create the worktree and **attach this chat** (no new Cursor window); skill continues jam here. `--new-window` forces legacy open. Non-glass Mac handoff opens Agents Window (`/glass`) then seeds `/prompt`.

### Per-scenario evidence (PR §4)

1. **Happy path — horizon:** sandbox schedule with 0–1 future dates → `ensure_restock_horizon` → exactly 2 upcoming Fridays, `updated_by=restock-horizon`. Hosted `/inventory` screenshot with two Friday columns.
2. **Idempotent:** second ensure adds 0 rows; existing `updated_by` unchanged.
3. **Exception week:** non-Friday already live → that week's Friday not added; later Friday fills horizon. Actuals/pins untouched.
4. **Config off:** weekday unset → ensure no-ops. Drawer register-only still works.
4a. **Delivery-day evening:** Friday after base closing → today no longer live → adds the Friday after next (not a no-op re-propose of today). Unit test + prod read-only simulation.
4b. **Operator removal sticks:** date whose latest `inventory_edit_log` schedule row is `clear` is never re-added; the next free Friday fills the horizon instead.
4c. **Page paints the new date:** console insert → `ensureOrderRecoFresh(..., { pageEdited: true })` requests refresh (trigger `restock-horizon`) despite the 60 s edit grace.
4d. **Preview / flag off:** `CONSOLE_PREVIEW=1` or `FEATURES.writeRestock=false` → no write.
4e. **Delivery slipped (operator 2026-10-20):** Fri 10-23 moved to Tue 10-27 → second date is Fri 10-30 (not 11-06); Mon registered without Move → nearest Friday; two slips → next free Friday. Python + vitest parity cases.
4f. **Config on the page:** header **Deliveries: Fri · 2 ahead** (`DeliveryCadenceEdit` → `setRestockCadenceAction`, weekday/Off + 1–4) writes both keys via `setConfig` (history in `inventory_edit_log`), tops up, refreshes reco. Screenshot on PR preview.

Slot model replaces the ISO-week rule: a Monday delay moved from the previous Friday would otherwise block that week's regular Friday.
5. **Glass attach:** unit tests for `prefer_attach_here` + `_run_one` skip `open_cursor_handoff` when glass; prints `CONTINUE_IN_THIS_CHAT=1`. Skill doc says continue jam in-chat.
6. **Legacy handoff:** non-glass path still opens worktree + glass + prompt (mocked).
7. `python3 scripts/verify.py --full` green.
8. **Demo before PR:** prod already has 2026-10-23 + 2026-10-30 → ensure no-op screenshot; sandbox insert evidence; operator OK.
9. **Post-merge:** read-only schedule check; first automatic add expected 2026-11-06 after 10-23 leaves the live window. Set prod `store_config` keys if still unset.

## Citations

- `core/order_reco.py:30` (`refresh_order_reco`)
- `agents/bhaga/scripts/daily_refresh.py:3900` (nightly refresh step)
- `apps/operator-console/lib/bq/writes.ts:17` (`setRestockSchedule`)
- `apps/operator-console/app/inventory/page.tsx:93` (page load / `nextDates`)
- `apps/operator-console/lib/bq/queries.ts:2549` (`nextDates` / `vw_order_reco_next_dates`)
- `core/store_config.py:46` (`get_config`)
- `cloud/webhook/handler.py:1288` (`_restock_set_schedule` MERGE shape)
- `core/migrations/052_order_reco_n_slots.sql:15` (live-date predicate)
- `scripts/new_requirement.py:532` (`_run_one` / open handoff)
- `scripts/start_pr_session.py:408` (`make_deeplink` / `open_cursor_handoff`)
- `.cursor/skills/jarvis-new-task/SKILL.md:44` (stop after launch)
- Docs: `docs/operator-console/ARCHITECTURE.md` § Inventory; `docs/FEATURE_FLAGS.md` (store_config keys); `agents/bhaga/knowledge-base/DOMAIN.md` restock schedule; `docs/contributing/dev-anywhere.md` (Agents Window); `RUNBOOK.md` config set; `check_doc_freshness.py`. CONTRIBUTING.md §4.

## Stubs

```python
# core/restock_horizon.py
WEEKDAYS = {"monday": 0, "tuesday": 1, ..., "sunday": 6}
AUTOMATION_BY = "restock-horizon"

def plan_horizon_additions(
    *, today: date, weekday: str | None, horizon: int, scheduled: Iterable[date],
    today_closed: bool = False, removed: Iterable[date] = (),
) -> list[date]: ...

def ensure_restock_horizon(store: str = "palmetto", *, by: str = AUTOMATION_BY) -> list[str]:
    """One input query (today, today_closed, scheduled, removed); guarded INSERT
    missing dates; append inventory_edit_log; return ISO dates added."""
```

```ts
// apps/operator-console/lib/inventory/restockHorizon.ts
export function planHorizonAdditions(opts: {
  todayIso: string; weekday: string | null; horizon: number; scheduled: string[];
  todayClosed?: boolean; removed?: string[];
}): string[];
// apps/operator-console/lib/bq/writes.ts — no-op in preview; always AUTOMATION_BY
export async function ensureRestockHorizon(store: string): Promise<string[]>;
// apps/operator-console/lib/bhaga/orderReco.ts
export async function ensureOrderRecoFresh(store, requestedBy, opts?: { pageEdited?: boolean });
```

## Robustness review (pre-build hardening)

| Risk | Resolution |
|---|---|
| Friday evening: today's closing is in, planner re-proposes today, INSERT no-ops → Friday after next missing until Saturday | `today_closed` shifts start to tomorrow (same rule as migration 051 view) |
| Holiday with no delivery: operator removes the Friday, automation re-adds it | Tombstone = latest `inventory_edit_log` `restock_schedule` row for that date is `clear` |
| Console insert logs an edit → 60 s grace suppresses page-open refresh → new column never paints | `pageEdited` bypasses grace; trigger `restock-horizon` |
| Read-only preview / restock writes disabled still write | `isConsolePreview()` guard + `FEATURES.writeRestock` gate |
| Concurrent page loads both insert | Guarded `INSERT … WHERE NOT EXISTS`; residual race yields a duplicate row that `vw_order_reco_next_dates` already `DISTINCT`s |
| Insert failure blocks reco | Per-date try/except + breadcrumb; `refresh_order_reco` wraps ensure |

```python
# scripts/new_requirement.py / start_pr_session.py
def prefer_attach_here(env: Mapping[str, str] | None = None) -> bool:
    """True when CURSOR_LAYOUT=glass and CURSOR_AGENT=1."""

def open_cursor_handoff(...):  # glass deeplink then prompt when not attach
def attach_worktree_here(folder: Path) -> None:  # best-effort cursor -r; print CONTINUE_IN_THIS_CHAT=1
```

```bash
python3 -m unittest core.test_restock_horizon scripts.test_new_requirement
cd apps/operator-console && npx vitest run __tests__/restock-horizon.test.ts
python3 scripts/check_plan_readiness.py --plan docs/plans/i416-restock-horizon-glass-attach.md
python3 scripts/verify.py --full
```

## Invariants

- Idempotent schedule INSERT only (no MATCHED rewrite of `updated_by`).
- America/Chicago for today / live-date window (same closing-aware predicate as `vw_order_reco_next_dates`).
- Never delete/move dates; never touch Actuals or tub pins.
- Config-off when `restock_delivery_weekday` unset — no silent Friday hardcode at runtime (defaults applied only via explicit `store_config` set in ops).
- Sandbox isolation for BQ writes in tests (mock datastore).
- Glass attach must not open a second Cursor window; editor/classic keeps a handoff path.

## Feature-flag decision

**No new code feature flag.** Tunables are `store_config` keys (user-preferences #29). Document in `FEATURE_FLAGS.md` under "Safe by construction / runtime config". Unset weekday = automation off (backward compatible).

## Docs lock-step

- `docs/operator-console/ARCHITECTURE.md` Inventory — Friday horizon ensure
- `docs/FEATURE_FLAGS.md` — `restock_delivery_weekday` / `restock_horizon_dates`
- `agents/bhaga/knowledge-base/DOMAIN.md` — schedule auto top-up
- `RUNBOOK.md` — `/bhaga-cloud config set` for the two keys
- `docs/contributing/dev-anywhere.md` + `.cursor/skills/jarvis-new-task/SKILL.md` — glass attach
- `python3 scripts/check_doc_freshness.py`

## Branch / PR mechanics

- Branch: `fix/want-to-work-on-improvement-of` (Issue #416)
- One PR → `--base main`; bot push; babysit; never self-merge; reply every review comment
- Cost: bind-pr + `pr_cost_ledger.py sync` after PR open
- Demo operator-visible `/inventory` before `gh pr create`

## Model routing

- M1–M3 implement/tests: Composer / Sonnet
- M4 docs + §4 screenshots: Composer
- Jam already on Opus; babysit reviews use Opus per playbook

## Milestone 1 — Pure horizon planner + Python ensure (Sonnet)

Add `core/restock_horizon.py` + `core/test_restock_horizon.py`. Wire `ensure_restock_horizon` at the start of `core/order_reco.refresh_order_reco` (covers nightly + any Python CALL path).

**Verify:** `python3 -m unittest core.test_restock_horizon`

**Pass criterion:** plan_additions cases (empty, one Friday, Thursday exception, off, idempotent) green; ensure mocked MERGE only inserts missing.

## Milestone 2 — Console ensure on `/inventory` (Sonnet)

TS `planHorizonAdditions` + `ensureRestockHorizon` write; call from `inventory/page.tsx` before `nextDates()`. Vitest parity with Python golden cases.

**Verify:** `cd apps/operator-console && npx vitest run __tests__/restock-horizon.test.ts`

**Pass criterion:** planner tests match Python fixtures; page calls ensure once per load (mocked).

## Milestone 3 — jarvis-new-task glass attach (Sonnet)

`prefer_attach_here`, attach banner, `--new-window`, glass-then-prompt handoff for non-glass. Update skill + tests.

**Verify:** `python3 -m unittest scripts.test_new_requirement`

**Pass criterion:** glass env skips `open_cursor_handoff`; prints `CONTINUE_IN_THIS_CHAT=1`; `--new-window` forces open.

## Milestone 4 — Docs, verify, demo, PR (Composer)

Docs lock-step; set sandbox/prod config keys for evidence; capture screenshots; `verify.py --full`; PR §4; babysit.

**Verify:** `python3 scripts/verify.py --full` && `python3 scripts/check_evidence_readiness.py --pr N`
