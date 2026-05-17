# Jarvis Build Progress

## Recurring Mistakes (read before every task)

| Mistake | Where the fix lives | Pre-check |
|---------|---------------------|-----------|
| Compared `2025` folder against itself (0 diffs = meaningless) | `orchestrator.py` `validate_against_benchmark()` | Verify shadow_folder_id != benchmark_folder_id |
| Copied folder structure from sealed `2025` benchmark | `derive_registry_from_return.py` | Never read `Taxes/2025` to decide what to create in `2025-test` |
| User correction acknowledged in conversation but not persisted | `.cursor/rules/jarvis.md` Hard Lessons + skill-evolution protocol | Every correction = a file write. Name the file or it didn't happen. |
| Asked user what could be self-checked (county, portal availability) | `chitra-playbook.md` Step 4 triage table | Derive from address/portal before asking |
| Validation done once at end instead of after each action | `orchestrator.py` `upload_and_validate()` | After each upload/folder creation, re-inventory and diff |

## BHAGA Agent (Tip Allocation & Payroll Prep)

**Status: Agent scaffolded 2026-04-18, ready for M1 (Square tips → sheet) on user confirmation of plan.**

Named after **Bhaga** (भग) — Vedic Aditya whose name derives from Sanskrit *bhaj* ("to apportion, divide, share"). The deity of just distribution of wealth and shares — the rightful portion due to each. Etymologically perfect for a tip-pool fair-share agent.

**Origin**: handoff doc at `get open/handoff-tip-allocator-agent.md` (chat: [Square ADP tip automation plan](b8a58719-e992-4051-954d-dbd513cf0f93)). Sibling-pattern reference: AKSHAYA (Square + Playwright + Sheets).

**What exists (scaffold only):**
- `agents/bhaga/` directory (`README.md`, `knowledge-base/README.md`, `scripts/README.md`)
- `agents/bhaga/scripts/notify.py` — BHAGA-tagged DM helper (transitional identity; see below)
- `.cursor/rules/bhaga.md` — agent behavior rule (auto-loads on `agents/bhaga/**`)
- Coordinator updated: `jarvis.md` architecture diagram, routing rule #4, naming table
- Top-level `README.md` updated with BHAGA agent section + new skills
- 4 new skill stubs created (`__init__.py` + `README.md`, no implementation yet):
  - `skills/square_tips/` — daily card tip totals via Square Payments API
  - `skills/adp_run_automation/` — per-employee daily hours via ADP RUN Time > Timecards (Playwright; no API for RUN small-business)
  - `skills/tip_pool_allocation/` — pure-function pool-by-day fair share math
  - `skills/tip_ledger_writer/` — daily ledger + period summary + ADP paste-block tabs into existing tip ledger sheet

**Existing skills BHAGA composes on**: `skills/browser/`, `skills/google_sheets/`, `skills/credentials/`, `skills/slack/`.

**What's next (BHAGA backlog — incremental milestones, each independently useful):**

1. **M1 — Square tips visible in sheet (~1–2 days)**: implement `skills/square_tips/` + minimal `skills/tip_ledger_writer/` slice that drops a "Tips Today" column into the existing Austin sheet. Replaces the manual Square dashboard lookup. **Blocked on user input**: Square access token, sheet ID + Google account, daily-tab header row, cash-tips column policy.
2. **M2 — Daily hours visible in sheet (~1 week, most fragile)**: implement `skills/adp_run_automation/`. Biggest unknown is RUN Time > Timecards DOM — requires one-time selector calibration during a live ADP session with the user. Also: MFA strategy (persistent cookie vs prompt-per-session). Selectors checked in to `agents/bhaga/knowledge-base/selectors/run_timecards.json`.
3. **M3 — Allocation computed (~2–3 days)**: implement `skills/tip_pool_allocation/` (pure function). Wire between M1 + M2 outputs. Pool-by-day fairness rule. Property-based tests for cent conservation + largest-remainder rounding.
4. **M4 — Paste-ready block emitted (~1–2 days)**: extend `skills/tip_ledger_writer/` with ADP Time Sheet Import format tab. End-of-period workflow: invoke BHAGA → open sheet → copy paste block → paste into RUN → approve.

**Open questions to resolve at M1 kickoff (per `bhaga.md` § Open questions)**:
1. Austin tip ledger sheet ID + which Google account owns it (Palmetto vs personal)
2. Daily tab header row (column names + sample row)
3. Cash tips tracked in sheet today? (BHAGA leaves untouched if yes)
4. ADP MFA enabled? (Determines M2 cookie strategy)
5. Employee name ↔ ADP file # mapping seed
6. ADP earnings code for tipped wages at this shop
7. Pay period schedule (weekly / biweekly / semi-monthly)

**Out of scope for v1 (per handoff)**: write-back to ADP Time Sheet Import (human pastes), cron/scheduled runs, multi-location in single invocation, per-day tip payout (tips ride paycheck), Square Team setup, replacing RUN with another time tracker.

**Risk acknowledgments (user-accepted)**: ADP ToS gray area (browser automation of own data with own credentials), UI fragility (~1 day of selector recal per ADP redesign), credential hygiene (Keychain only, session cookies in Jarvis state not repo), MFA friction (intentional human-in-the-loop on first login per session).

**Coordination with AKSHAYA**: AKSHAYA also extracts Square data (orders/recipes via Playwright today, on backlog to migrate to API). BHAGA's `skills/square_tips/` only handles `GET /v2/payments` — no overlap with AKSHAYA's catalog/orders extraction. When AKSHAYA migrates to Square API, both agents will share auth + pagination + retry logic by adding sibling functions to `skills/square_*/`.

**BHAGA Slack identity — REAL (2026-04-19)**: BHAGA now has its own Slack app + bot user, provisioned end-to-end via `skills/slack_app_provisioning/` + Playwright (cursor-ide-browser MCP). App ID `A0AU05T2YS0` in workspace Jarvis. Both tokens (xoxb + xapp) in Keychain under service `jarvis-bhaga` (accounts `SLACK_BOT_TOKEN_BHAGA` and `SLACK_APP_TOKEN_BHAGA`). DM channel `D0ATWHSA14J`. `config.yaml` `slack.agents.bhaga.identity_mode = "real"`; `[BHAGA]` text prefix automatically disabled. First DM sent from the real BHAGA bot user verified delivered. The earlier "transitional" period (CHITRA bot + `[BHAGA]` text prefix) lasted ~1 day and is now closed.

**Hard Lesson #0 (added 2026-04-18) — paid off (2026-04-19)**: User correction "why are you making me create a Slack app manually when you have all these skills?" led to building `skills/slack_app_provisioning/` + the Playwright drive. Net result: future agents (Narada, Vidura, etc.) get their real Slack identity in one command, no manual web-UI homework. Lesson is in `.cursor/rules/jarvis.md` Hard Lessons.

**user_model skill (skill addition, 2026-04-19)**: New skill at `skills/user_model/` builds a predictive model of how the user thinks. Captures preference signals from every user turn (heuristic phrase detection — Fork 1A), surfaces inline for confirmation (Fork 2A), persists confirmed preferences to a single auto-loaded markdown file `.cursor/rules/user-preferences.md` (Fork 3A) under 4 sections (Communication style / Design principles / Domain context / Decision history). Cross-references Hard Lessons via the `Source` column rather than restating (Fork 5: single source of truth). Skill not agent (Fork 4A) — global, every Jarvis agent reads the same file. Seeded with 7 style + 14 principles + 12 domain facts + 7 decisions distilled from accumulated chats. Capture protocol codified in `jarvis.md` § "During a Session". Raw corpus (gitignored) at `skills/user_model/data/corpus.jsonl` for v2 distillation. v2 deferred: programmatic `query.py` (not needed while file fits in context), `digest.py` for periodic re-distillation.

**tip_pool_allocation skill complete (M3 — 2026-04-20)**: Pure-function pool-by-day fair share allocator at `skills/tip_pool_allocation/adapter.py`. Enshrines the two non-negotiable invariants from `bhaga.md`:
  - Rule #5 (no period-pooling): `employee_share_for_date = (employee_hours_on_date / total_team_hours_on_date) * tip_pool_for_date`, summed across dates. Never pool the whole period.
  - Rule #11 (deterministic rounding): largest-remainder method with lexicographic tie-breaking on employee id. Cent conservation exact.

  Public API:
  - `allocate(daily_tips: dict[date_iso -> cents], daily_hours: dict[(emp, date_iso) -> hours]) -> AllocationResult`
  - `AllocationResult.per_day` — one row per productive (date, employee)
  - `AllocationResult.per_period` — summed hours + tips per employee
  - `AllocationResult.flags` — edge cases (tips-with-no-hours, hours-with-no-tips)

  22 unit tests at `skills/tip_pool_allocation/test_adapter.py`, all passing:
  - Cent conservation across 200 random property-based inputs
  - Determinism across 10 runs with same inputs
  - Pool-by-day fairness invariant (high-tip-day worker gets more despite equal period hours)
  - Real Austin week-of-3/23 data yields sum-of-shares = $288.47 exactly (matches Square dashboard)
  - Edge cases: empty inputs, tips-no-hours, hours-no-tips, negative-raise, non-integer-cents-raise

  Built 2026-04-20 during a Playwright browser-context outage (HL #11 workspace restart cycle) — pure-Python skill, no browser dep, so parallel productive work while waiting.

**Square dashboard tip extraction (M1 part 1 of 2 — proven end-to-end, 2026-04-19)**: Square Developer Console access blocked because Palmetto runs on a single corporate Square account managed by the chain owner (store owners get dashboard access only). User emailed Square rep for elevated access; in the meantime, built the dashboard-automation backend per Hard Lesson #5 ("browser is a stepping stone"). Full proven from-scratch flow:

  1. **Credentials captured** via `skills/browser/collaborative.py` interceptor (multi-step variant for Square's email→Continue→password 2-step login). Stored in Keychain at service `jarvis-square-palmetto`, account `adi@mypalmetto.co`. Registered in `skills/credentials/registry.json` as `square_palmetto_login`.
  2. **From-scratch login** via Playwright using only Keychain creds (no browser-profile cookie reuse). Verified: logout → /login → email → Continue → password → Sign in → /dashboard/.
  3. **Sales Summary export pipeline**: navigate → switch Report type to Days (one-time, sticky setting) → click Export icon → click Export in popover → CSV downloads to `extracted/downloads/sales-summary-{start}-{end}.csv`.
  4. **CSV parser** at `skills/square_tips/dashboard_backend.parse_csv()` handles UTF-8 BOM + multi-line quoted header cells. Returns canonical schema `{date, tip_total_cents, card_tip_cents, cash_tip_cents, payment_count, source}` per day.
  5. **Verified output for week of 2026-03-23 to 2026-03-29**: 7 records totaling $288.47 (matches dashboard exactly), 131 transactions across the week.

  **New artifacts**:
  - `skills/square_tips/adapter.py` — public `daily_tips()` interface; auto-picks API backend if PAT in Keychain, dashboard backend otherwise. Per Hard Lesson #5, the API migration is a backend swap, not a caller change.
  - `skills/square_tips/dashboard_backend.py` — Playwright playbook builder + CSV parser + Keychain credential resolution.
  - `skills/square_tips/selectors/dashboard.json` — calibrated selectors with `last_verified: 2026-04-19` for login, export trigger, export confirm, report type pill, days option, apply button, date range pill, tips row.
  - `agents/bhaga/knowledge-base/square-exports/` — sample CSV checked in as a parser fixture.

  **TODOs before M1 ships**:
  - Date range setter (currently relies on session-persistent default; calibrate the date picker UI for arbitrary weeks)
  - Loop over `iter_weeks()` in the playbook (one CSV download per Mon-Sun window)
  - `skills/tip_ledger_writer/` minimal slice — drop a "Tips Today" column per date into the Austin sheet (still need sheet ID + tab header from user)
  - `agents/bhaga/scripts/pull_tips.py` — M1 orchestrator wiring the above

**Square app provisioning skill (skill addition, 2026-04-19)**: New skill at `skills/square_app_provisioning/` mirrors `skills/slack_app_provisioning/` for Square Personal Access Tokens. 10-step Playwright playbook for `developer.squareup.com/apps` + Locations page → captures `EAA...` PAT + `sq0idp-...` app id + `L...` location id → `register.py` stores PAT in Keychain (`SQUARE_ACCESS_TOKEN_<STORE>` under `jarvis-square-<store>`) + writes `agents/bhaga/knowledge-base/store-profiles/<store>.json` + sends BHAGA confirmation DM. Multi-store from day one (`--store austin` / `--store houston`). Trust model = PAT (full account, single-user) per user fork pick 2026-04-19; OAuth migration to `PAYMENTS_READ`-scoped flow documented as v2 path in skill README.

**Multi-agent Slack listener (skill update, 2026-04-19)**: `skills/slack/listener.py` now `--agent`-aware. Per-agent listeners use `jarvis-<agent>` Keychain service + `/tmp/jarvis-slack-inbox-<agent>.json` + reply via the agent's bot. `skills/slack/inbox_processor.py` scans every `/tmp/jarvis-slack-inbox*.json` and tags pending actions with the originating agent. `skills/slack/ensure_listening.py` reads `slack.agents.*` from `config.yaml` and starts one listener per agent with `identity_mode: "real"` (currently BHAGA). Default behavior unchanged for backward compat.

**BHAGA manifest fix (2026-04-19)**: Slack default for new bots since 2022 has Messages tab read-only — users see DMs from the bot but can't reply. Fix: added `app_home: { messages_tab_enabled: true, messages_tab_read_only_enabled: false }` to both the skill default manifest and BHAGA's per-agent override. Re-imported into existing app A0AU05T2YS0 via Playwright. Verified directly on the App Home settings page that the "Allow users to send messages" checkbox is now ticked.

**Slack app provisioning skill (skill addition, 2026-04-18)**: New skill at `skills/slack_app_provisioning/` automates the full Slack app creation flow for any new Jarvis agent — manifest generation + Playwright-driven web admin steps + Keychain token storage + config wiring + first-DM-as-real-bot, all in one. Replaces the prior manual procedure (which was a Hard Lesson — see jarvis.md Hard Lesson #0). Reusable for every future agent: `python -m skills.slack_app_provisioning.provision --agent <name>` then `python -m skills.slack_app_provisioning.register --agent <name> --bot-token xoxb-... --app-token xapp-...`. Per-agent manifest overrides at `agents/<name>/setup/slack-app-manifest.yaml`. The new "Adding a New Agent" checklist in jarvis.md now lists this as Step 4 (mandatory, not optional). The manual setup README at `agents/bhaga/setup/README.md` was demoted to a fallback procedure with a banner pointing at the skill.

**Always-listening daemons (skill addition, 2026-04-18)**: New idempotent helper at `skills/slack/ensure_listening.py`. Single command starts and verifies BOTH:
- Slack Socket Mode listener (`skills/slack/listener.py`) — instant push from Slack to `/tmp/jarvis-slack-inbox.json`
- Inbox processor (`skills/slack/inbox_processor.py`) — polls inbox, acknowledges on Slack, writes to `/tmp/jarvis-pending-actions.json`

Idempotent: detects alive vs stale PIDs and only starts what's needed. Default 8h runtime, 30s poll interval. Logs to `/tmp/jarvis-listener.log` and `/tmp/jarvis-inbox-processor.log`. Use at every session start: `python skills/slack/ensure_listening.py` (or `--status` to check without starting). Replaces the prior need to manually start two separate scripts and remember the right flags. Recommended addition to `jarvis.md` § "Session Continuity" boot checklist.

---

## AKSHAYA Agent (Inventory Forecasting & Ordering)

**Status: v1.9 shipped 2026-05-12 PM — Blade dropped, B6 bumped 120 → 130.** User asked to remove Blade from active planning (still parseable in `DAY1_REFERENCE_INVENTORY` for historical closing reports; just no longer in `HQ_BASES`). Capacity bumped to reflect one fewer item sharing the cooler. Layout now: 8 items at rows 28-35, TOTAL at row 36 (was 37), notes header stays at row 39. `build_sheet_v3.py` now clears gap rows from `TOTAL_ROW+1` to `NOTES_HEADER_ROW-1` so the old layout's TOTAL/Blade ghost cells get blanked on each push when item count changes. K-helpers (K28-K31) auto-derive their `$D$28:$D$N` ranges from `total_range_D` which uses `ITEM_END_ROW = ITEM_START_ROW + NUM_BASES - 1`. Test ranges in `test_allocation.py` that hardcoded `range(28, 37)` were swapped for `range(ITEM_START_ROW, ITEM_END_ROW + 1)`; `USER_TUNED` set became a property so it picks up the live range. Tests still 66 green. Result with B6=130, B12=5: Order Total = 56 (up from 46 with B6=120), Post-Order = 132.3 tubs vs cap 130 (over by 2.3 — well within "few days over OK"), 5 of 7 orderable items cluster at 20-21d, Açaí still stuck at 27d, Ube stuck at 38d. To re-include Blade later: add 'Blade' back to `HQ_BASES` in `forecast_v2.py` and push again.

**v1.10.1 patch 2026-05-12 PM — "C is truth" reconciliation + series-fallback rate path.** User noticed Pog's corrected rate of 0.282/day still didn't match physical reality (today's C=5.80 from manual count vs snapshot's 1.80 on 5/11 — a 4u gap meaning the entire 5/5-5/11 cluster of "1.80-1.99" readings was bad data; closer was likely measuring residual from a near-empty separate batch). Codified a new principle: **the Current Stock column (C) is absolute truth; when the snapshot can't be reconciled to today's C within wobble tolerance, the snapshot is wrong**.

Three changes:
1. **7 new overlay entries** for Pog 5/5-5/11 set to `None` (= delete reading). These were dropped rather than smooth-interpolated because we don't know what really happened that week — only that today's count proves the snapshot was wrong.
2. **`compute_per_item_consumption` series-fallback**: when the latest snapshot date has an item missing (e.g. overlay-dropped tail cluster), fall back to the latest available value in the in-window series for `raw_latest`. Without this, deleting a bad tail makes rate=0 even with valid earlier data. Output dict gets `current_stock_source='series-fallback'` for transparency.
3. **Pog rate**: 0.282 → 0.071/day (matches the pre-anomaly 5/2→5/3 burn). Live sheet pushed; Pog correctly reclassified as stuck (DoS=41 days), capacity redistributed across the 6 free items. Free-item DoS cluster tightened further: 26-29 days, mean 26.7, **stdev 1.1**.

Reconciliation table (today's C vs 5/11 snapshot, post-corrections):
- All items consistent within ±0.65u except: Pog +4.0u gap (resolved by this patch), Açaí -8.0u gap (unresolved — could be a big-consumption day today, or 5/11 still over-counted; surfaced to user for decision).

Test updated: `EqualizeDoSV18.test_equalize_dos_python_simulation_matches_design` now accepts 2 or 3 stuck items (Açaí + Ube + optional Pog) since the stuck-set varies with active corrections. Still 73 tests green.

**v1.10 shipped 2026-05-12 PM — closing-report corrections overlay.** User noticed Mango's Avg Use/Day was suspiciously high at 1.15/day (twice Açaí's rate, which doesn't match shop reality). Forensic dive into the 14-day snapshot surfaced ~9 manual data-entry errors hiding behind the restock-aware downward-moves estimator:

1. **Truck day identified**: 2026-04-30 is THE delivery day for the whole HQ snapshot — 7 of 8 active bases show synchronized +1u jumps that day (Coconut +4.8, Tropical +3.1, Mango +8.1, Pitaya +9.1, Matcha +2.1, Ube +1.1, Pog +1.6, total +29.8u). Every other "restock" my code flagged was a counter wobble (closer typed a slightly-higher value the next morning) or a typo.
2. **The Mango 5/4 typo** was the most consequential: closing report says 7.99 vs. surrounding values 18.00→17.99→16.85. Almost certainly a missing leading "1" — should be 17.99. The phantom −10.01u drop alone inflated Mango's rate from ~0.5 to 1.15/day, and was driving an 18-tub order recommendation that the user couldn't justify physically.
3. **Açaí 4/30 truck count was late**: closer wrote 11.00 on the 4/30 form (pre-truck count) and 41.30 on the 5/1 form (post-truck count). My code recovers the +30u as a "restock" but charges 2.25u of pre-truck "consumption" to the rate. Re-anchoring 4/30 to 41.30 removes the phantom burn.
4. **Other small wobbles** (Açaí 4/28 +1.10, 5/3 +1.54, 5/11 +3.95; Mango 4/29 +1.80; Ube 5/3 +1.00, 5/5 +1.05; Pog 5/1 +1.25) all look like the closer over-counting by one tub, then writing the right number the next day. Each got a corrected value.

**Overlay mechanism**: new constant `CLOSING_REPORT_CORRECTIONS` in `forecast_v2.py` keyed by `(YYYY-MM-DD, item)` → corrected_value. Applied inside `load_inventory_timeseries()` immediately after ClickUp parse. ClickUp source data untouched (reversible by deleting the entry). Value of `None` deletes a reading. 9 corrections currently live in the dict.

**Rate impact** (before → after, units/day):
- Açaí: 0.941 → 0.656 (−30%)
- Mango: **1.147 → 0.556 (−51%)**
- Ube: 0.075 → 0.000 (low-velocity item; all real moves are sub-noise after smoothing)
- Pog: 0.282 → 0.282 (correction shifted the fake-restock by one day, net zero)
- Coconut/Tropical/Pitaya/Matcha: unchanged (no corrections needed, single clean truck-day jump each)
- **Total D: 4.62 → 3.36/day (−27%)**

**Allocation impact**: with new D values pushed to the live 2026-05-12 tab, the equalize-DoS allocator re-clusters 7 of 8 free items at 24-29 days DoS (mean 25.7d, stdev 1.6d) — extremely tight. Mango's order drops from 18 to 7 tubs. Ube classified as stuck (D=0 = no measurable consumption). Order total: 67 tubs; post-order: 134.4 (4.4 over B6=130 cap, well within "few days over OK").

**Tests grew 66 → 73** (`ClosingReportCorrectionsV110` +7 covering dict shape, well-known fixtures, loader actually applies overlay, Mango rate < 1.0 after fix, None-value delete escape hatch). Loosened `EqualizeDoSV18.test_equalize_dos_python_simulation_matches_design` upper bound on T_refined from 30 → 50 since lower total D pushes the refined target upward — that's a positive feature of the overlay, not a regression.

**Design note**: this overlay is the "data layer" complement to v1.9's "static D" design. D stays static across in-sheet C edits (per the v1.9 invariant below), but data corrections do change D when the script is re-run because they live in the snapshot loader, not the sheet. To add a correction, edit `CLOSING_REPORT_CORRECTIONS` and re-run `forecast_v2.py` + push D to the sheet. To revert, delete the entry.

**v1.9 design invariant — D is intentionally static across in-sheet C edits.** Reaffirmed 2026-05-12 PM when user replaced Açaí's C value (37.3 → 24.5) by hand and asked why Avg Use/Day (D) didn't change. Reason: D = sum-of-downward-moves over last 14 days / 14, requires the full 14-day timeseries (which lives in `inventory_snapshot.json`, not in the sheet). Translating that into a sheet formula would require pushing all ~14 days × 9 items of daily closings into hidden cells. Instead, D is computed once per refresh in `forecast_v2.compute_per_item_consumption` and written as a static value to D28:D35. In-sheet C edits flow through E/F/G/H (live formulas) but not D — by design, so single-day spikes or manual corrections don't whipsaw the 14-day smoother. To recompute D with a new same-day reading, run `forecast_v2.py` after adding the reading to the snapshot. Documented in the sheet's notes block (row 44) and in `akshaya.md` § "Consumption-rate calculation".

**v1.8 shipped 2026-05-12 PM — equalize-DoS allocation.** Third major iteration of the same day, in response to user's observation that v1.7's proportional-to-D allocation produced widely-different DoS values per item. New goal: maximize the count of items whose Days-of-Supply land within ±4 days of a shared target, rather than just proportional capacity slicing. Algorithm: `T_init = B6 / SUM(D)` → classify items as stuck (`C > D × T_init`) or free → `T_refined = (B6 − SUM(C_stuck)) / SUM(D_free)` → free items order toward `D × T_refined` (with B12 as MOQ floor); stuck items order 0 (+ Δ) and drain naturally. Four new helper cells (K28-K31) make the math live-recomputing in-sheet. Summary row 2 now surfaces "Equalize-DoS Target", "In-band count (±4d)", and "Outliers" so the user sees which items are diverging from the cluster. ROUND replaces CEILING (per user "we can order less"), keeping SUM(F) within ±1 tub of B6 in expectation. Tests grew 58 → 66 (`EqualizeDoSV18` +8). Migration: no `--reset-config` needed; the formula change is structural (E-row formulas, not values). B12 stays at user's tuned 5; semantic note: B12 now means "min order per FREE item" — stuck items skip it.

**v1.7 shipped 2026-05-12 PM — capacity-driven allocation (replaces % target).** Same-day follow-up to v1.6: B6 switched from "Target % of Initial Inventory" (percentage) to "Total Tub Capacity" (absolute units, default 120). The user's planning knob is now "we can fit 120 tubs in the cooler" rather than "stock to 105% of where we were 3 weeks ago" — concrete, not derivative. Per-item target = `B6 × (D / SUM(D))`, still floor-clamped to B12, still +Δ. `SUM(F)` may exceed B6 when items are already overstocked; summary row 1 flags as `⚠ OVER CAPACITY`. Initial Inventory column (B28:B36) stays anchored to the 3-weeks-ago closing but is now INFORMATIONAL only — kept as a "where were we 3 weeks ago" sanity check. Tests grew 49 → 58 (`CapacityModelV17` +9 covering B6 default = 120, label, E-formula references `$B$6` directly, zero leaks of `SUM(B)*B6/100`, forecast title, summary, B7 unchanged, B-column still anchored). Migration: first push uses `--reset-config=B6` to force-overwrite the carried-over `105` percentage with `120` tubs.

**v1.6 shipped 2026-05-12 PM — post-event growth model overhaul.** Three interlocking changes pushed together once Media Day / Grand Opening passed and the user noted the model needed to grow up:

1. **Trailing-window growth rate** replaces the static "5% WoW + +50% event bump" model. `B5` is now an in-sheet formula that derives a geometric-mean weekly growth rate from `$B$7` (window length, default 3) and the displayed weekly daily-avg table (`D15:D21`). With current Square data the 3-week trailing rate is +28%/wk (vs the old static 5% + 50% event bump that was permanently "on"). User can edit `B7` in-sheet to retune; B5 recomputes live.
2. **Initial Inventory re-anchored** from day-1 (3/25 channel message) to **per-item closing report at the Sunday before the trailing window starts**. With N=3 the anchor is 4/19; so `Target = SUM(B) × B6%` now means "stock to X% of where we were 3 weeks ago" rather than the increasingly stale opening-day baseline. New helpers in `forecast_v2.py`: `compute_trailing_growth_rate()`, `compute_window_start_anchor_date()`, `resolve_inventory_at_anchor()`. The day-1 dict is preserved as `DAY1_REFERENCE_INVENTORY` (with back-compat alias `INITIAL_INVENTORY`) and is used as a fallback when no closing exists at/before the anchor.
3. **Event columns dropped entirely** — `EVENT_WEEK_START` / `EVENT_BUMP` removed from `forecast_v2.py`; sheet cells `B7`/`B8` repurposed (B7 = Window Weeks, B8 = Initial Inventory Anchor Date — info); DoS formula simplified (no more `devent` / `em` terms). A one-time migration is needed when refreshing the first v1.6 dated tab — use `build_sheet_v3.py --reset-config=B7` to force-overwrite the stale event-date value that would otherwise carry over from the v1.5 tab.

The user also flagged a WoW perception issue (4/27=529 < 4/20=532 shows -0.6%, looks wrong against the upward trend). The math is correct — pinned by new `WoWGrowthMathPinned` tests — and the trailing rate explicitly addresses the perception: smoothed over N weeks, the "real" trend is +28%/wk over the last 3.

Tests grew 32 → 49: `WoWGrowthMathPinned` 3, `TrailingGrowthRate` 5, `InitialInventoryAnchoring` 5, `EventColumnsRemovedInV16` 4 (net +17).

Today's push (2026-05-12 snapshot, tab `2026-05-11`): 9 bases ordered totalling 69 units, post-order 146.3 (target 128 = 122 × 105%). Mango leads at +18u (rate 1.147/day); Açaí and Ube ordered 0 (already at or above their share). Order Total +20 vs the 4/21 plan as the 5/4 event-week traffic showed up in the data (+65% WoW). 8 of 9 bases logged restocks in the 14-day rate window — Açaí biggest single restock at +30.3u on 5/1 — all correctly excluded from the consumption-rate sum via the new downward-moves method.

**Status: v1.4 shipped 2026-05-12 — consumption-rate rewrite (restock-aware). The old `(initial − current) / days_elapsed` estimator broke once HQ started restocking individual items (every base had ≥1 restock between 4/22 and 5/11, confirmed in the 5/11 refresh). Switched to "sum of downward-only moves over last 14 days / 14" in `forecast_v2.compute_per_item_consumption`. Restocks (positive jumps) contribute 0 to the consumption sum, so the rate stays honest regardless of how many shipments landed. Avg Use/Day (D column) became a STATIC VALUE in the sheet (the new computation needs the full timeseries which isn't in-sheet). Auto-denoising of current stock removed (the monotone-decrease invariant it relied on is gone). Restock detection surfaced informationally in the sheet notes. Tests grew 21 → 26 (`ConsumptionRateRestockAware`, 5 new). v1.3 (Δ column), v1.2 (Initial Inventory rename), v1.1 (target-driven allocation) all preserved on top.**

Created 2026-04-16. Named after the Akshaya Patra (inexhaustible divine vessel of food).

**What shipped (v1):**
- Data pipeline: ClickUp closing reports (search by `tag="closing submission"`) → Square orders (Playwright CSV export via `skills/square_tips/dashboard_backend.py`) → `forecast_v2.py` → `build_sheet_v3.py` → Google Sheet `1Ut3fmgaKFrU1Vwnfufx_83OWY-YpfLriRw68owP4uQY` (Palmetto account).
- **Formula-driven sheet**: every derived number (order qty, post-order stock, days of supply, totals) is a formula referencing configurable cells. User edits `B5`/`B6`/`B7`/`B8`/`B9` or a `D27:D35` override and the whole sheet recomputes. No script re-run required for knob changes.
- **Weekly-compounding Days-of-Supply**: per-row `ARRAYFORMULA(LET(SEQUENCE,POWER,SCAN,XMATCH))` simulates day-by-day consumption with weekly-compounding growth (B5) + event bump (B7/B8), then finds the first day the cumulative consumption crosses the post-order stock. Works around Google Sheets's LET-doesn't-broadcast gotcha.
- **Robust free-text parser** (`parse_inv` in `forecast_v2.py`): handles observed typos (`^` → `%`), commas (`3 boxes, 75%`), and multi-part additive entries. Any numeric token after `+` / `,` is treated as a percentage.
- **Current-stock selection — trust latest, denoise only when off**: `forecast_v2.py::compute_per_item_consumption` defaults to `raw_latest` for `current_stock`. Denoises to `median of last 7 reports` ONLY when `raw_latest > median × 1.30 AND > median + 0.5 units` — the invariant being "no restocks ⇒ inventory monotone non-increasing". Downward drift is never denoised. Per-item source (`latest` vs `denoised`) + reason is carried through to JSON + sheet notes. Items with `current > max_capacity` (day-1 underfill) are flagged `noisy=true`; rate clamps to 0. The sheet's D-column is a formula off C, so user overrides cascade instantly.
- **Order quantities are whole units**: `ROUND(..., 0)` because inventory is discrete.
- **Target-driven allocation + manual Δ override (v1.1 → v1.2 → v1.3 2026-04-21)**: E (order qty) and F (post-order stock) decided off a target driven by B6% of total Initial Inventory, with a per-item manual delta column the user can type into.
  ```
  TARGET_TOTAL   = SUM(Initial) × B6/100               -- total budget driven by target %
  per-item target = TARGET × D/SUM(D)                   -- proportional to Avg Use/Day
  clamped_target = MAX(B12, per-item target)            -- floor-only; NO upper cap
  per-item E     = MAX(0, CEILING(clamped − C + Δ, 1))  -- whole-unit order qty, ≥ 0 (Δ = col G)
  per-item F     = ROUND(C + E, 2)                      -- actual post-order stock shown
  ```
  - `B12` is the **min-units safety floor** (default 6). Applied **unconditionally when Δ=0**: every base fills to ≥ B12 units regardless of historical usage. No per-item upper cap — B-column is Initial Inventory (day-1 stock), not storage capacity, so B6=120% legitimately means "target 20% MORE than we started with".
  - `Δ` (col G, default 0 per item) is the v1.3 **manual override knob**. Applied AFTER the floor, so a negative Δ intentionally can drop F below B12 (explicit user decision). When Δ=0 for all items, output is bit-identical to v1.2 (regression-protected by `scripts/test_allocation.py`). Use cases: "order 3 extra Açaí this week" → +3; "skip Pog this time" → -6.
  - CEILING on the order qty guarantees `F ≥ clamped_target + Δ` (ROUND could leave F just under floor for small-decimal cases). SUM(F) overshoots TARGET_TOTAL by a handful of units from the floor + CEILING; both values + Σ Δ are shown in sheet summary row `A24`/`A25` so user can dial B6/B12/Δ to taste.
- **Current scope**: bases only (granolas removed per user direction). Target = 95% of *total initial inventory*; safety floor = 6 units/base applied unconditionally (when Δ=0); per-item Δ override in col G for last-mile tweaks; everything else is proportional-to-usage.
- **Regression tests** (v1.3 2026-04-21): `scripts/test_allocation.py` — 21 tests covering (a) Δ=0 regression vs v1.2, (b) Δ semantics (+/-/huge/undershoot-floor/zero-use), (c) edge cases (SUM(D)=0 fallback, CEILING-prevents-floor-underflow), (d) **sheet-formula structure** (runs build_sheet_v3 fresh, asserts Δ is in G, DoS in H, E formula references G, default deltas are 0, TOTAL row sums Δ). Catches column-drift bugs before the sheet is pushed. Run with `python3 scripts/test_allocation.py`.
- **New reusable artifact — `skills/square_tips/dashboard_backend.py`**: Square dashboard CSV export was graduated into BHAGA's reusable skill; AKSHAYA will call the same module once weeks-iteration is added.

**MCP tool extensions earned this session** (pushed into `~/.cursor/mcp-servers/mcp-gdrive/`):
- `gsheets_update_cell` now defaults to `valueInputOption: "USER_ENTERED"` so formulas evaluate. Pass `rawInput: true` to write literal strings.
- **`gsheets_batch_update`** — bulk write up to hundreds of cells in one API call (formulas supported by default). This unblocks formula-driven sheets at scale.
- **`gsheets_add_tab`** (2026-05-12) — create a blank tab in an existing spreadsheet (snapshots, dated history tabs).
- **`gsheets_duplicate_tab`** (2026-05-12) — clone an existing tab (preserves all formulas + formatting + user-tweaked config cells) under a new title. This is the workhorse for history-preserving refreshes — every refresh duplicates the prior canonical tab to a new dated tab, then overwrites only the value cells.
- All four reflected in `~/.cursor/skills/google-sheets-ops/SKILL.md` (including a new "History-Preserving Refreshes" pattern section that documents the dated-tab-per-refresh workflow for any agent producing recurring snapshots).

**Knowledge base** (`agents/akshaya/knowledge-base/`):
- `refresh-procedure.md` — canonical "update numbers as of today" runbook (includes consumption method, sheet config knobs, DoS formula shape)
- `storage-capacity.md` — max capacity reference from day-1 closing report
- `square-catalog.md` — Square menu structure reference
- `clickup-inventory-latest.json`, `forecast-v2-latest.json` — last refreshed data snapshots

**AKSHAYA backlog:**
1. **ClickUp Chat MCP** — today the max-capacity reference is pulled from a manual channel dump. Build an MCP so AKSHAYA can fetch first/latest channel messages live. (Also unblocks other agents that need channel context.)
2. **Square REST API migration** — replace Playwright CSV export with direct API. Shared plumbing with BHAGA's `skills/square_tips/api_backend.py` (when access is granted). Hard Lesson #5 ("browser is a stepping stone") applies.
3. **Weeks-iteration in `dashboard_backend.py`** — right now AKSHAYA triggers one export per invocation; generalize to loop `iter_weeks()` so a full-history refresh is one call.
4. **BYO ingredient decomposition** — current model correlates base consumption vs total orders. Next level: modifier-level breakdown (BYO is 28% of volume) so we can forecast *ingredient* consumption, not just base consumption.
5. **Recipe-enhanced correlation** — layer HQ recipe table on top of order history for precision forecasting (deferred from Phase 1).
6. **Multi-store generalization** — Houston opens September 2026. Store identity must come from config, not code. Test the current config-cell pattern against a second store before opening.
7. **Spoilage model** — fresh fruits have shelf-life windows; extend DoS formula to clamp on `min(depletion_day, spoilage_day)`.
8. **Calibration loop** — weekly compare predicted vs actual consumption and surface drift.

**Hard Lessons earned this session (captured in `.cursor/rules/akshaya.md`):**
- **Consumption rate: anchor on endpoints, not windowed averages.** The cleanest shape is `(max − current) / days_elapsed`. This ignores every intermediate closing report, so mid-window typos can't contaminate the rate at all. Previous approaches ("positive drops only", "first-window vs last-window avg") were strictly weaker. *Evolved from windowed-averages → endpoint-anchored on 2026-04-21.*
- **Current stock: trust the latest reading, denoise only when it violates the invariant.** Previous v1 always used `median of last 7 closing reports` as the denoised current. User pushback: "I want the latest value unless it clearly looks off — we only apply smartness when there's no restocking and the number can't be real." Final rule: `current = raw_latest` UNLESS `raw_latest > median × 1.30 AND raw_latest − median > 0.5 units` (both). Asymmetric (downward drift is expected under consumption and never denoised) and dual-threshold (the 0.5u floor prevents over-correcting small values). As of 2026-04-21, 8 of 9 bases use raw_latest; only Blade denoises (raw 2.30 vs ~1.0 median, impossible w/o restock). *Evolved from always-denoised → trust-latest-except-when-off on 2026-04-21.*
- **Align the days-elapsed window with the date the "current" value represents.** When `current` is the raw latest reading, `days_elapsed = snapshot_date − opening_date`. If you ever switch `current` to a multi-day median, the divisor should still be the snapshot date because the median is *still anchored at today* (it's just noise-filtered). The D-column formula `(Max−Current) / (B9−B11)` stays valid either way; the value in C is where the "smartness" is applied, not in the denominator.
- **Static recomputed sheet cells are a dead-end** for iterative planning. Formula-driven + named config cells wins every time.
- **LET doesn't broadcast in Google Sheets.** Wrap in `ARRAYFORMULA`. Use `POWER()` not `^`.
- **Partial weeks skew averages.** Filter to complete 7-day windows only for weekly volume displays.
- **Noisy items should be flagged, not silently zeroed or negative-rated.** When denoised current > max cap, flag and surface for manual review. Let the user be the arbiter with a D-column override.
- **ROUND over FLOOR for order qty.** Current-stock values include partial-container remainders (e.g. 22.45 = "22 full + 0.45 partial"), so post-order stock exceeding max by a fraction is rounding on the partial digit, not real overfill. `FLOOR` underfills high-velocity items. Wrap with outer `MAX(0, …)` to prevent negative orders when denoised current > max.
- **Invert the driver direction when the user's semantics change**: v1 had `E` as the primary formula (equalized-DoS allocation) and `F = C + E` derived. v1.1 per user spec: "F decides first (target % × D), E = F − C" — same math algebraically (proportional-to-D ≡ equal-DoS), but the spoken order of ops matches how the user thinks. When the user explains the model out loud, let their narrative order drive which cell holds which formula — doesn't change the numbers, but makes later conversations ("why is F this number?") map cleanly onto one formula, not an inverse.
- **Safety floors cap at max, don't stack on top**: a "min 6 units per base" floor must clamp at per-item Max when Max < 6. Formula: `MIN(Max, MAX(floor, proportional))`. If you write `MAX(floor, MIN(Max, …))` instead, items with Max < floor blow up past Max. Test with a small-max item before shipping. *(2026-04-21 update: this lesson assumed "Max" was a real physical cap. See next lesson for when it isn't.)*
- **Question the semantics of "max" columns before clamping against them** (2026-04-21 v1.2): v1.1 capped per-item allocation at B (called "Max Cap"). User pushed back when B6=120% failed to increase stock above initial and the B12 floor wasn't honored for items with Initial<6. Root cause: B was never a real storage ceiling — it was day-1 stock from the opening channel message. The "Max Cap" label invented a constraint that didn't exist in reality. Fix was 3-part: (1) **rename** B-column to "Initial Inventory" so every downstream reader sees the truth, (2) **remove** the `MIN(B, …)` clamp from allocation so B6% can legitimately exceed 100% and B12 floor is honored unconditionally, (3) update the code constant (`MAX_CAPACITY` → `INITIAL_INVENTORY`) so future edits don't reintroduce the misconception. **Before writing a clamp, ask: is this value a real hard limit, or just a reference point? If in doubt, ask the user. Naming lies faster than code.**
- **CEILING > ROUND when a floor must be honored** (2026-04-21 v1.2): with whole-unit orders + decimal current stock, ROUND on `target − current` can leave F just below the floor (Ube target=6, C=3.9 → ROUND(2.1)=2 → F=5.9, clipped). CEILING rounds order qty up, guaranteeing F ≥ target. Cost: SUM(F) overshoots target by up to ~1 unit/item. Worth it when the floor is a safety constraint, not a target.
- **Manual override columns are a force multiplier on formula-driven sheets** (2026-04-21 v1.3): user asked to "add a column such that I can add positive/negative delta which is reflected post applying the formula". The Δ column (G) is additive to the existing target expression (`CEILING(target − C + Δ)`) — a one-character change in the formula adds full manual control without disturbing any existing math. Default value 0 means the column is a no-op until the user engages it; the whole sheet stays reactive; the v1.2 regression path is preserved. The lesson: when a user wants manual overrides on a derived value, plumb them through as an additive term in the existing formula, not by swapping in a parallel "if user entered N use N else compute" branch. Simpler, testable, and zero cognitive cost when ignored.
- **Write the test that catches the bug you almost shipped** (2026-04-21 v1.3): while adding Δ col G, the easy bug is column-drift — DoS still sitting in G, or E formula still pointing at the old column. `test_allocation.py::SheetFormulaStructure` regenerates `sheet-updates-v3.json` from the current code and asserts (a) G27 = "Δ Adjust", (b) H27 = "Days of Supply", (c) E28 formula contains "G28", (d) default G-values are "0", (e) G-rows 28–36 don't contain "ARRAYFORMULA" (would mean DoS leaked into G). That's 5 tests for the 5 ways column-shift could go wrong. These assertions are cheap to write and catch the exact mistake most likely to slip through manual review.
- **Models invalidate silently across long sessions; refreshing data must include refreshing assumptions** (2026-05-12 v1.4): the `(initial − current) / days_elapsed` rate model was correct on 4/21 (no restocks yet, monotone-decreasing). By 5/11 it was silently wrong — every base had been restocked at least once between 4/22 and 5/11. The math still ran (no crash, no negative rate after `max(0, …)`), it just produced low-or-zero rates that no longer reflected real consumption. **Lesson**: when an estimator depends on an external invariant ("HQ ships only on opening day"), encode the invariant in code (assert / detect / flag) AND re-validate it in every refresh. Don't trust that "the assumption from 3 weeks ago still holds." The fix here was a restock-robust estimator (downward-moves only); the deeper habit is detect-and-surface-when-your-model-is-invalidated, not just refresh-the-numbers. Also: if a value was a formula because it could react to user edits, and the new computation needs out-of-sheet inputs, accept the downgrade to static value rather than fake-reactivity with a broken formula.
- **Browser MCP selection: `user-playwright` for production scraping, `cursor-ide-browser` only for testing webapps under development** (2026-05-12, refresh attempt): both MCPs expose `browser_*` tools with near-identical signatures. The IDE-embedded one is for testing frontends under development (per its own server-use-instructions); it has a different browser context, doesn't share Playwright's persistent profile, and won't see saved Square login cookies. The user-playwright MCP is where Keychain credentials are wired, where `skills/browser/portal_session.py` connects, and where the dashboard selectors are calibrated. Naming similarity is a trap — codified the rule in `akshaya.md` Operational Gotchas section so it survives across sessions. **Habit**: when two MCPs offer the same-named tool, grep the rule file for which one this agent uses BEFORE the first navigation call.
- **Skill-evolution should fire on assumption-invalidation, not just on explicit user corrections** (2026-05-12, meta): when I detected restocks in the 5/11 data, I updated the runbook doc (§4f) but didn't update PROGRESS Hard Lessons, didn't update the code (still had the broken `(B−C)/days` formula), and didn't write a test. User had to remind me. Skill says proactive triggers include "When the agent notices friction, a workaround, or a gap in the current skill being followed — flag it immediately rather than waiting for the session to end" — discovering "the model assumption from last session no longer holds" is exactly that signal. The lock-step checklist (5 durable artifacts: agent rule, runbook, scripts, PROGRESS.md, global skills) must be walked end-to-end on every invariant-breaking discovery, not just when the user types "update the skill". Treat invariant-violations as first-class evolution triggers.

**Context from prior research ([Proposal Research](d05ccd64-972f-4548-b34a-c03513a24f11)):**
- Austin store opened March 23, 2026 (soft opening, ~42 orders/day, $513/day avg)
- Targets: $4K weekday, $7K weekend sales
- Square POS, recipes controlled by HQ, DoorDash + Uber Eats integrated
- Product mix: BYO 28%, Signature Bowls 34%, Smoothies 34%
- Emergency grocery runs ($229 in 3 weeks) = inventory forecasting failures
- MarketMan subscription at $396/mo — existing pain point
- HQ supplies: acai, branded packaging, granola, specialty items with multi-day lead times
- Knowledge bank: `get open/knowledge-bank/raw-intake.md` (17 entries)

---

## Current Phase (CHITRA)
**14 portals DONE. 31 docs uploaded to Drive. 25/33 adjusted files match (76%).**

- Final registry: 34 documents, 21 folder paths
- Raw validation: 24/37 (64%), but user removed 4 from tracking (iso-tracker, Moss Adams, 1095-C, 2024 return)
- Adjusted: 25/33 = 76%

**Portal download status:**
| Portal | Status | Docs | Notes |
|--------|--------|------|-------|
| Schwab | DONE | 2 | 1099 Composite (acct 965) + Account 3771 Statement. ISO Disposition Survey = last (user's Google Sheet, needs DASH transaction cross-ref across Schwab + E-Trade). |
| E-Trade | DONE | 4 | 1099 Consolidated (DASH), Stock Plan Supplement, Mailing Group Letter, De Minimis Letter (AABA) |
| Robinhood | DONE | 1 | 1099 Consolidated (Securities and Crypto). Login: aditya.2ky+hood@gmail.com, MFA via app push. |
| Wells Fargo | DONE | 1 | 1098 Mortgage Interest Statement (acct 5503) |
| County Property Tax (Fort Bend) | DONE | 2 | Tax Statement + Receipt (2025) from Fort Bend County. Acct 8118640020010907, CAD Ref R555090, 1414 Crown Forest Dr. |
| San Mateo County | DONE | 2 | 2024-2025 + 2025-2026 Property Tax Bills. Acct 104-140-030, 211 Golden Eagle Ln Brisbane. Cloudflare bypass: click checkbox in Turnstile iframe. |
| Homebase | DONE | 4 | Form 941 Q4, Form 940 Annual FUTA, W-2 Lindsay (Employee), W-3 Transmittal. Login: adi@mypalmetto.co (Palmetto Chrome Passwords CSV), MFA via SMS to phone ending 0038. |
| Chase | DONE | 1 | 1098 Mortgage Interest (acct 7737, primary residence). Login: aditya2kxbiz, MFA via Chase mobile app push. |
| Obie Insurance | DONE | 4 | 2024 + 2025 full policies and declarations. Login: aditya.2ky@gmail.com, email PIN. Policies: OAN024977-00 ($1,991), OAN024977-01 ($2,270). |
| MH Capital (InvPortal) | DONE | 1 | 2025 K-1 for MH Sienna Retail II LLC. Login: aditya.2ky@gmail.com at mhcapital.invportal.com. |
| BCGK InvestorCafe | DONE | 2 | K-1 + Preferred Return Distributions xlsx ($6,250 = 4 quarterly × $1,562.50). Login: aditya.2ky@gmail.com at 23192bcgk.investorcafe.app. Site finicky — refresh after login. 7-digit email 2FA. |
| Ziprent | DONE | 1 | 1099-MISC ($74,450 rental income). Login: aditya.2ky@gmail.com at app.ziprent.com/auth/login. Tax Forms page under account dropdown menu. |
| FBCAD (Fort Bend) | DONE | 2 | 2025 + 2026 Appraisal Notices (shows HS homestead exemption active). Public site, no login. esearch.fbcad.org property search → Appraisal Notice PDF link. |
| Just Appraised | DONE | 1 | 2025 Texas Form 50-114 Homestead Exemption Application (#27782044, R555090). Login: aditya.2ky@gmail.com at taxpayer.justappraised.com. Auth0 fails in Cursor Electron browser but works in Playwright Chrome. |

**Incremental validation (codified in jarvis.md #13):**
After every upload, run `python agents/chitra/scripts/validate_upload.py --slack` to diff shadow vs benchmark.
Current: 25/33 adjusted files match (76%). 8 files remaining.

**User removed from tracking:** iso-tracker JSON, Moss Adams estimate, DoorDash 1095-C, 2024 Federal Return

**9 remaining files:**
| File | Category | Action Needed |
|------|----------|---------------|
| 2025 W-2 - DoorDash - Aditya | W-2s & Employment | User uploads from DoorDash Workday |
| 2025 W-2 - Texas Childrens Hospital - Kajri | W-2s & Employment | User/Kajri uploads |
| 2025 Student Loan Tax Info - Kajri | W-2s & Employment | User/Kajri uploads |
| ISO Disposition Survey CSV | Brokerage/Schwab | Google Sheet cross-ref DASH across Schwab+E-Trade (deferred to end) |
| Rastegar K-1 email | Partnerships | Expected Aug 2026, not available yet |
| 2025 Bank Transactions - Brisbane Rental CSV | Brisbane Rental | User exports from bank |
| ~~2025 Texas Form 50-114 Homestead Application~~ | ~~Primary Residence~~ | DONE — Downloaded from Just Appraised portal (Playwright Chrome). |
| 2025 Donum Charitable Lending Note | Charitable | User provides |
| 2025 Palmetto Business Transactions - Copilot Export | Business | User exports from Copilot |

**Skill persistence (new this session):**
Portal navigation configs created/updated for ALL 13 portals:
- `agents/chitra/scripts/portals/` — 14 config files (9 existing + 5 new)
- `agents/chitra/knowledge-base/download-strategies.md` — 4 download methods, MFA patterns, Cloudflare bypass
- Each config has `verified` date and `verified_actions` list
- Generalizable: given prior-year return + passwords + questionnaire, system can replay to 73%+

**File naming convention**: `{year} {Form Type} - {Issuer} {Account Details} - {Description}.{ext}`
Helper: `agents/chitra/scripts/naming_convention.py`

**Corrections from validation:**
- Wells Fargo 1098 moved from Primary Residence → Brisbane Rental (was in wrong folder)
- All 8 files renamed to match benchmark naming convention (year-first format)
- Property tax: benchmark has "$9,757 PAID" in name (amount matters)

**Playwright recovery lesson (codified in jarvis.md #11):** Kill Chrome browser-profile processes + remove lock files, NOT the MCP server.

**Idle state fix (codified in jarvis.md #12):** Never go idle after sending a Slack message. Always check for replies + continue working. Only stop when user says "done" or "stop".

**Slack communication architecture (3 layers):**
1. Socket Mode Listener (`skills/slack/listener.py`) — instant WebSocket receive, auto-handles commands
2. Inbox Processor (`skills/slack/inbox_processor.py`) — polls every 2 min for 4h, classifies messages, acknowledges on Slack, writes to `/tmp/jarvis-pending-actions.json`
3. AI Agent — reads pending-actions.json at start of every turn + between major actions

**On session start:** Check `cat /tmp/jarvis-inbox-processor.pid` and restart if needed. Also restart listener if needed.

## Last Session (2026-04-05, session 3)
- **Questionnaire answers processed** — user-answers-2025.json created and applied
  - Kajri left Stanford Childrens → Texas Childrens Hospital (new employer)
  - Primary residence: 1414 Crown Forest Drive, Missouri City, TX
  - Homestead exemption filed and approved
  - Business employee (Homebase payroll) for Palmetto Superfoods
  - Charity: Donum replaces prior
  - Retirement: 403b through Texas Childrens (provider TBD)
- **Partnership cities added** — Auburn CA, Houston TX, Austin TX from user input
- **K-1 status tracking** — k1_received flag: MH Sienna received, only Austin TX pending
- **RPC name normalization** — ISSUER_BRAND_MAP: "RPC 5402 South Congress Partners LLC" → "RPC 5402 South Congress LLC"
- **Folder derivation fixes** — 5 validation iterations, 8/22 → 18/22 folder match
  - new_home updates existing PRIMARY RESIDENCE docs (no folder duplication)
  - Business employee docs mapped to correct "08 - Business - {name}" folder
  - taxYear field added to final registry
- **2025-test recreated** 5 times during iterative validation
- **Remaining diffs analyzed** — all 4 are expected:
  1. `Kajri - Texas Childrens Hospital` vs `Kajri [NEED W-2s]` (we know employer)
  2. `Auburn CA - Lincoln Way` combined vs benchmark split (user confirmed same)
  3. `Texas Childrens Hospital [NEED DOCS]` vs `Fidelity [NEED DOCS]` (skipped)

## Prior Session (2026-04-05, session 2)
- **Slack long-polling loop** — AI agent stays alive and responsive to Slack
  - `skills/slack/wait_for_input.py` — blocks until Slack message arrives (checks every 5s) or timeout
  - `skills/slack/inbox_processor.py` — background daemon (4h), polls inbox every 2min, classifies messages, acknowledges on Slack, writes to `/tmp/jarvis-pending-actions.json`
  - 3-layer architecture: Listener (instant) → Processor (2min) → AI (active polling)
  - Rule in `jarvis.md`: always check pending-actions + inbox before every action
- **Derivation code fixes** — reduced folder diffs from 14 missing/11 extra to 7 missing/5 extra
  - `_parse_address()` / `_abbreviate_street()` — proper address parsing
  - K-1 subfolders get `[NEED K-1]` suffix
  - "Expenses" → "Expenses Partnership" renaming
  - New categories: `09 - Tax Payments & Extensions`, `06 - Retirement Accounts`
  - Remaining 7 diffs = all need questionnaire answers

## Prior Session (2026-03-28, continued)
- **Derive-first pipeline refactor** — all folder paths now derived from user data, never from benchmark
  - `derive_folder_tree()` + `ISSUER_BRAND_MAP` added to `derive_registry_from_return.py`
  - 19 nested folder paths derived from 22 documents (was: 8 flat categories)
  - `drivePath` field set on every document during derivation
  - Subfolder naming: `{person} - {brand}` for W-2s, `{brand}` for 1099s, `{city} Rental - {address}` for properties, entity name for K-1s, business name embedded in category
  - `ISSUER_BRAND_MAP` normalizes legal entities to brands (e.g. `Charles Schwab & Co., Inc` → `Schwab`)
- **`create_shadow_folders.py`** — rewritten to accept `--registry` flag, support N-level folder nesting (was limited to 2)
- **`orchestrator.py`** — critical validation fix
  - `validate_against_benchmark()` now inventories `2025-test` (shadow) and compares against `2025` (benchmark)
  - Safety check: rejects if shadow_folder_id == benchmark_folder_id
  - `resolve_folder_id()` maps drivePath to shadow folder IDs
  - `run_pipeline()` wires full sequence: registry → create folders → init tasks → Slack notification
- **`process_answers.py`** — imports `derive_folder_tree`, `rebuild_folder_tree()` method re-derives paths after answers
- **`onboard_from_return.py`** — updated to use `derive_folder_tree()` instead of flat folder list
- **Hard Lessons codified** to persistent files:
  - `.cursor/rules/jarvis.md` — Hard Lessons section + concrete feedback routing table + skill-evolution hook
  - `.cursor/rules/chitra-playbook.md` — subfolder derivation rules in Section 1.3
  - `PROGRESS.md` — Recurring Mistakes table at top

## Last Session (2026-04-05)
- Built collaborative browser session framework (`skills/browser/collaborative.py`)
  - JS credential interceptor: captures form fields on submit, stores in sessionStorage+localStorage to survive redirects
  - Slack-based user notification: AI navigates browser, notifies user via Slack to enter creds
  - Takeover flow: AI can request user help, watch for "done" signal, learn navigation patterns
  - Learning persistence: stores navigation patterns in `agents/chitra/knowledge-base/learnings/`
- Added `collaborative_login` step type to portal plan generator (`base.py`)
  - `generate_plan()` now accepts `credential_mode="collaborative"|"keychain"`
  - Plan markdown renders collaborative login sub-steps for AI execution
- Wired `CollaborativeSession` into `TaskRunner` (`run_portal_tasks.py`)
  - `ensure_credentials()` now supports `method="collaborative"|"slack"`
  - When creds missing + collaborative mode: marks task as ready with `credential_mode=collaborative`
  - Plan generation passes credential_mode through to step generator
- Built Chrome Password Manager → Keychain import pipeline (`credentials/import_from_chrome.py`)
  - Reads Chrome CSV export, matches URLs to known Jarvis portal patterns
  - Shows matches table with existing Keychain status, asks for confirmation
  - Bulk stores all confirmed entries in Keychain, deletes CSV immediately
  - Mapped 9 portals with URL patterns for matching
- Imported 7 portal credentials from Chrome in one shot:
  - Schwab, E*Trade, Wells Fargo, Fidelity, Robinhood, Homebase, Chase
  - HSA provider still missing (need to identify which provider user has)
- Attempted collaborative browser credential capture for Wells Fargo:
  - Learned: JS interceptor loses state on SAML redirects (cross-origin navigation)
  - Learned: Polling form fields directly via Playwright is more reliable than event listeners
  - Learned: Chrome CSV import is far more efficient for bulk credential collection
  - Collaborative browser model still valuable for: first-time logins, stuck navigation, CAPTCHA handling

## Session (2026-03-28)
- Restructured repo from flat CHITRA layout to Jarvis agent/skill hierarchy
- Renamed workspace: Tax Strategies -> Jarvis
- Renamed GitHub repo: chitragupta -> jarvis
- Moved 29 git-tracked files to new locations (core/, skills/, agents/chitra/)
- Moved gitignored files (knowledge-base JSON, scripts/personal/, 2025/ data)
- Updated imports in all 8 scripts (sys.path bootstrap + core.config_loader)
- Created Jarvis coordinator rule (.cursor/rules/jarvis.md)
- Created Slack skill (skills/slack/adapter.py) with send_message, read_replies, request_otp
- Stored Slack bot token in macOS Keychain (service: jarvis)
- Added Slack MCP to user-level ~/.cursor/mcp.json
- Tested Slack connection: DM sent successfully to workspace owner
- Updated all configs (config.template.yaml, config.yaml, .gitignore, .cursor/mcp.json)
- Fixed Playwright MCP config (`--profile` -> `--user-data-dir`, explicit nvm PATH)
- Moved Playwright MCP to user-level `~/.cursor/mcp.json`
- Installed Chromium for Playwright MCP
- Added stronger session continuity rules so new chats resume from files, not chat history
- Verified direct Google Drive API access via local config/token refresh path
- Added local Drive parity tooling: reusable inventory command + shadow diff script
- Added direct Google Drive folder-creation helper for shadow-folder setup
- Cleaned up document-registry.json: normalized all drivePaths to numbered convention, deduplicated IDs (30-33 → 34-37), removed status suffixes from folder names, removed incorrect Expenses Partnership folder, updated emptyFolders
- Queried Google Sheet — confirms 31 documents tracked, all match registry
- Upgraded chitra-playbook.md: prior-year return is now the primary bootstrap input (not manual registry maintenance)
- Added "Handling User Design Feedback" protocol to jarvis.md
- Rewrote create_shadow_folders.py to be registry-driven (no hardcoded folder names) — works for any CHITRA user
- Refreshed benchmark inventory at `extracted/drive-2025-inventory.json`
- Improved derive_registry_from_return.py fuzzy matching (6.5% → 48.4% match rate)
  - Added issuer normalization (strip EINs, account numbers, legal suffixes)
  - Added docType aliasing (Consolidated 1099 → 1099, Form 1098 → 1098, etc.)
  - Generic issuer matching (Property Manager, County Tax Assessor → matches actual names)
- Created return-profile.schema.md — canonical JSON schema for tax return profiles
  - CHITRA uses this schema when parsing any user's tax return text
  - Covers all standard forms: 1040, Schedules A-E, 8889, 8949, 8582, K-1s
- Created generate_questionnaire.py — produces 35 friendly layperson questions
  - 19 Confirmation questions (did prior-year items change?)
  - 16 Discovery questions (life events the return can't predict)
  - Categories: Jobs, Investments, Rental, Partnerships, Business, Charitable, Health, Home, Life Events, Retirement, Education, Tax Payments
  - Each question explains WHY it's asked and WHAT to do if the answer is yes/no
- Created onboard_from_return.py — full new-user pipeline
  - Input: PDF (local or Drive ID) or existing profile JSON
  - Step 1: Extract text via pdfplumber
  - Step 2: Print parsing prompt + schema for CHITRA to produce profile JSON
  - Step 3: Derive registry + questionnaire from profile
  - Works for ANY user — no hardcoded names or entities
- Current match analysis: 15/31 registry docs derived from prior-year return alone (48.4%)
  - Remaining 16 are genuinely new-year events (new home, new CPA, DONUM note, employer payroll docs, retirement accounts, homestead exemption, etc.)
  - These are exactly the questions the questionnaire asks

### Prior Sessions (2026-03-27)
- Completed CHITRA v1: Phases A-D (git init, knowledge capture, browser automation, README)
- CPA email drafting and Homebase document handling
- Uploaded employer tax docs (W-2, W-3, Form 941, Form 940) to Drive

## What's Next (v2 backlog)
1. ~~Add channels:join scope~~ DONE — bot invited to #all-jarvis manually
2. Install Playwright MCP and test with a county CAD site (public, no login) — IN PROGRESS
   - Fixed: `--profile` → `--user-data-dir`, added env PATH for nvm, moved to user-level MCP config
   - Chromium browser binary installed
   - Remaining issue: Playwright MCP descriptors appear on disk, but runtime MCP tool list has not exposed `user-playwright` yet
3. Populate credentials/portals.yaml and Keychain entries for each portal
4. Test full OTP flow: Playwright login -> Slack OTP request -> continue
5. Fix validation gaps (docType normalization, Sheet tab names, estimates field names)
6. **Shadow folder validation (BLIND PARITY mode)**: Build `Taxes/2025-test` entirely from CHITRA's knowledge, automation, and user conversations — never look inside real `Taxes/2025`
   - Real folder is sealed; only opened for a final scored comparison
   - Derive folder structure from `drive-folder-convention.md` + `document-registry.json`
   - Derive filenames from naming conventions + document metadata
   - Ask user for any missing input data, configs, or credentials
   - Done: benchmark inventory captured (sealed), diff tooling ready, folder-creation helper ready
   - Next: create `2025-test` root folder, then derive and build subfolder structure from knowledge base
7. **New-user onboarding pipeline**: PDF → extracted text → CHITRA parsing → profile JSON → registry + questionnaire
   - Schema: `agents/chitra/knowledge-base/schema/return-profile.schema.md`
   - Questionnaire: `agents/chitra/scripts/generate_questionnaire.py`
   - Pipeline: `agents/chitra/scripts/onboard_from_return.py`
   - Tested: 35 questions generated, 22 docs derived, 8 folder categories

## North Star Vision
CHITRA's goal is fully autonomous tax document collection for ANY user:
1. User hands CHITRA their prior-year tax return PDF
2. CHITRA parses it into a structured profile (CHITRA-the-AI is the parser)
3. CHITRA derives 60-70% of the expected documents from the return
4. CHITRA asks ~35 friendly questions to fill the remaining 30-40% (life events, changes)
5. From answers, CHITRA autonomously figures out WHERE to get each document:
   - County property tax sites (derived from address → county lookup)
   - Broker portals (credentials in Keychain)
   - Employer HR portals
   - Insurance company sites
6. CHITRA navigates those sites (Playwright), downloads documents, uploads to Drive
7. User only provides: the PDF, answers to plain-English questions, and occasional permissions
8. End result: 100% populated Drive folder structure matching what a human would build

Current state: Steps 1-4 built and tested. Steps 5-6 now PROVEN — Playwright MCP works, Schwab login + tax form discovery succeeded, county CAD property lookup autonomous. Steps 7-8 (download + upload) are built but need first real download test.

## Live Questionnaire Exercise Results (2026-03-28)
Simulated new-user onboarding using only `profile-2024.json` + user Q&A (no peeking at real registry).

**Starting point:** 22 docs derived from 2024 return alone (48.4% of real 31-doc registry)
**After 6 questions + answers:** 34 docs identified (~97% coverage of real registry)
  - 13 from return alone (no questions needed)
  - 10 from user answers (6 questions total)
  - 11 CHITRA would fetch autonomously via Playwright (zero user questions)

**Key learnings persisted to chitra-playbook.md:**
1. Check-yourself-first principle: never ask what you can check via portal/bank/public site
2. Question triage table: self-check vs bank-derived vs address-derived vs must-ask vs user-provides
3. Smart follow-ups: address → county → portal URL → homestead (auto-derive chain)
4. Employer HR portals = user provides (too much SSO friction)
5. Match user's tone, use names not "taxpayer/spouse"
6. Gmail is a document source — CPA correspondence + charitable docs (priority skill)
7. Bank transactions reveal insurance providers and property managers
8. Status reports > more questions ("Downloaded X, Y. Z isn't available yet — want me to email?")

**Portal registry created:** `credentials/portals.yaml.template` with 20+ portals mapped:
  - 8 Playwright-automatable (brokers, banks, county sites, insurance, payroll)
  - 4 Playwright+OTP (brokers with MFA)
  - 2 user-provides (employer HR with SSO)
  - 3 email-based (CPA, charitable, K-1 notifications)
  - Gmail skill identified as high priority (came up 2x in exercise)

## Immediate Next Steps (prioritized by impact)
1. **Run full portal automation** — 8/9 portals have creds; run `prepare_all()` and execute plans via Playwright
2. **Test actual PDF download** — click download on Schwab/E*Trade, save file, upload to Drive
3. **Identify HSA provider** — last missing credential; add URL pattern to import script
4. **Build Gmail skill** — high priority, came up twice in questionnaire exercise (charitable docs, CPA correspondence)
5. **Build county tax bill scraper** — county tax assessor sites for actual tax payment receipts
6. **Verify Slack Socket Mode** — test WebSocket connection for real-time OTP delivery
7. **Score against real registry** — run final diff of exercise-built registry vs actual document-registry.json

## Playwright E2E Tests (2026-03-28)
Successfully tested autonomous document discovery and login:
1. **County CAD** (public, no login) — searched by address
   - Found property record: appraised value, homestead exemption confirmed
   - Deed history, taxing jurisdictions, property details all extracted
   - Full autonomous discovery: address in → property data out, zero user interaction
2. **Charles Schwab** (authenticated, no MFA) — logged in with Keychain credentials
   - Navigated to Statements & Tax Forms
   - Found **1099 Composite and Year-End Summary - 2025 AVAILABLE** for both accounts
   - Account selector works: can switch between accounts
   - Clean logout verified
3. **E*Trade** (authenticated, MFA required) — logged in with Keychain credentials
   - Login successful, but MFA triggered (SMS to registered phone)
   - No email OTP option available (only SMS or alternate phone)
   - OTP request sent to user via Slack DM — deferred (user offline)
4. **Credential workflow validated**: store_credential.py → macOS Keychain → PortalSession.get_credentials() → Playwright fills login
5. **Slack OTP notification**: sent DM to user requesting OTP code, confirmed delivery

## Components Built This Session (2026-03-28)
1. **Slack Socket Mode listener** (`skills/slack/listener.py`)
   - Push model: WebSocket connection, Slack sends events instantly (no polling)
   - Writes OTP replies to `/tmp/jarvis-otp/{portal}.json` for instant pickup
   - `request_otp()` in adapter.py auto-detects Socket Mode vs polling fallback
   - Requires: App-Level Token (`xapp-...`) + Socket Mode enabled in Slack app
   - TODO: user needs to generate app-level token and enable Socket Mode + event subscriptions

2. **Portal automation framework** (`skills/browser/portal_session.py`)
   - `PortalSession` class: credential retrieval, OTP orchestration, download staging, Drive upload, registry update
   - Reusable for ANY portal: `session = PortalSession("Schwab")`
   - `get_credentials()`: reads from macOS Keychain
   - `request_otp()`: sends Slack DM, waits for reply (push or poll)
   - `stage_download()` + `upload_all()`: batch upload to Drive with auto-naming
   - `_update_registry()`: marks docs as received in document-registry.json
   - `list_keychain_portals()`: shows all stored portal credentials

3. **Portal navigation framework** (`agents/chitra/scripts/portals/`)
   - `base.py`: portal loader, plan generator, registry — discovers all modules, generates step-by-step AI plans
   - 9 structured portal modules, each exporting `PORTAL_CONFIG` dict:
     - `schwab.py`: iframe login, 1099 Dashboard SPA, multi-account selector
     - `etrade.py`: mandatory SMS MFA, stock plan + brokerage sections
     - `county_property_tax.py`: public CAD search, address → county derivation
     - `robinhood.py`: React SPA, hCaptcha risk, 1099-DA for crypto
     - `fidelity.py`: brokerage + retirement + HSA, NetBenefits split
     - `wells_fargo.py`: mortgage 1098, transaction export, email MFA
     - `chase.py`: hash-based SPA routing, email MFA available
     - `hsa_bank.py`: generic multi-provider (HealthEquity, Optum, Fidelity, etc.)
     - `homebase.py`: payroll forms (W-2, W-3, 941 quarterly, 940)
   - Architecture: navigation knowledge (like DB drivers) is checked in; user's portal manifest (which ones they use) stays in config.yaml (gitignored)
   - `list_portals()` discovers all modules; `generate_plan()` produces step-by-step AI execution plans from any config
   - `format_plan_markdown()` renders a human/AI-readable plan with quirks, selectors, and code snippets

4. **Answer-processing pipeline** (`agents/chitra/scripts/process_answers.py`)
   - `AnswerProcessor` class: takes derived registry + questionnaire answers → final registry + portal task list
   - `apply_confirmation()`: process yes/no answers for prior-year items
   - `add_from_life_event()`: one answer triggers multiple documents (e.g. "new home" → mortgage 1098 + property tax + homestead + HUD-1)
   - 12 life event handlers: new_home, home_sold, new_employer, employer_left, new_brokerage, new_rental, rental_sold, business_employee, new_partnership, state_move, new_charity, homestead_exemption
   - `generate_portal_tasks()`: matches each document to available navigation modules, produces prioritized task list
   - Automation levels: fully_automated, check_then_ask, needs_module, email_skill, user_provides
   - Tested: 22 derived docs + 3 life events → 31 docs, 12 portal tasks (5 fully automated, 1 check-then-ask, 3 need modules, 1 email, 2 user-provides)

6. **Slack adapter improvements**
   - `request_otp()` upgraded: phone_hint parameter, Socket Mode auto-detection
   - Config updated: `slack.primary_user_id` and `slack.dm_channel` stored
   - MFA-via-Slack rule added to chitra-playbook.md (CRITICAL: always notify via Slack, never rely on IDE)

7. **Credentials stored in Keychain**
   - jarvis-schwab, jarvis-etrade (usernames stored securely, never in git)

8. **Portal task runner** (`agents/chitra/scripts/run_portal_tasks.py`)
   - `TaskRunner` class: full orchestration loop for credential → plan → execute → status
   - `check_all_credentials()`: shows which portals have creds stored vs missing
   - `ensure_credentials()`: checks Keychain → if missing, asks user via Slack DM
   - `request_credentials_via_slack()`: sends DM asking for username then password, stores in Keychain, deletes credential messages from Slack history
   - `prepare_task()` / `prepare_all()`: checks creds + generates execution plans for all portal tasks
   - `resolve_portal()`: fuzzy-matches issuer names to portal modules (e.g. "Charles Schwab & Co" → schwab)
   - `mark_complete()` / `send_status_summary()`: Slack notifications for progress tracking
   - CLI: `--check` (cred status), `--plan <module>` (single plan), `--prepare` (all tasks), `--interactive` (ask for missing creds)
   - Tested: 3 ready (schwab, etrade, county), 7 blocked (missing creds) — exactly matches Keychain state

9. **Collaborative browser session** (`skills/browser/collaborative.py`)
   - `CollaborativeSession` class: AI drives browser, user assists when needed
   - JS credential interceptor: captures form fields on submit/click/Enter, persists to sessionStorage+localStorage
   - Slack notifications: notify user to enter creds, request takeover when stuck, resume after user helps
   - Learning persistence: stores navigation patterns in per-portal JSON files
   - Plan generation: `generate_login_plan()` produces step-by-step instructions for AI agent
   - Integrated into `TaskRunner` via `credential_mode="collaborative"` parameter

10. **Chrome → Keychain import pipeline** (`credentials/import_from_chrome.py`)
    - Reads Chrome Password Manager CSV export
    - Matches URLs against 9 known portal patterns (extensible)
    - Shows confirmation table with existing Keychain status
    - Bulk stores in Keychain, securely deletes CSV
    - One user action (Chrome export) → all portal creds stored

## Blockers
- ~~Playwright MCP is configured and Chromium is installed, but runtime MCP tool availability is inconsistent~~ **RESOLVED** — Playwright MCP is fully operational (tested 2026-03-28)
- ~~Slack Socket Mode not yet enabled~~ **RESOLVED** — App-Level Token generated, Socket Mode enabled, `message.im` event subscribed
- E*Trade requires SMS MFA — no email option, blocks fully autonomous login until Gmail skill or Slack Socket Mode is operational
- Some county .gov sites block automated browsers via Cloudflare — use CAD search sites (.org) instead
- ~~Portal credentials partially populated~~ **RESOLVED** — 8/9 portals credentialed via Chrome CSV import (only HSA provider missing)
- Google Drive MCP read-only auth path is failing with a Google 403 — Drive work uses direct API helpers instead

## Completed Steps
- [x] CHITRA v1 — Phases A-D (commits 7cea51d → fa1e88c)
- [x] Jarvis architecture restructure
  - Workspace renamed: Tax Strategies -> Jarvis
  - GitHub repo renamed: chitragupta -> jarvis
  - Directory hierarchy: core/, skills/, agents/chitra/
  - 29 files moved via git mv
  - All imports updated
  - jarvis.md coordinator rule created
  - Slack skill created and tested
  - Config templates updated with slack section

## Decisions Log
- 2026-03-27: Public repo (open-source the framework)
- 2026-03-27: Passwords via macOS Keychain, never plaintext
- 2026-03-27: Playwright MCP for browser automation, Slack MCP for OTP
- 2026-03-27: No PII in any git commit
- 2026-03-28: Restructure to Jarvis coordinator + agent/skill hierarchy
- 2026-03-28: jarvis/ folder conflict resolved by renaming old to jarvis-legacy/
- 2026-03-28: Slack MCP in user-level ~/.cursor/mcp.json (not workspace — secrets)
- 2026-03-28: Portal playbooks under agents/chitra/ (domain knowledge, not generic skill)
- 2026-03-28: sys.path bootstrapping for imports (pyproject.toml deferred to v3)
- 2026-03-28: Shadow-folder validation is a BLIND test — never look inside real `Taxes/2025`, build everything from CHITRA's own knowledge + user input
- 2026-03-28: If mirror validation hits unresolved discrepancies, pause and ask the user instead of guessing
- 2026-03-28: Mirror-validation diffs should continuously drive Jarvis's next-step prioritization
- 2026-03-28: CHITRA's primary input for bootstrapping a tax year should be the prior-year federal/state returns — parse every schedule/form/issuer, derive the document checklist and folder structure from it, then pull docs autonomously using saved credentials. The registry is derived output, not manual input.
- 2026-03-28: Expenses Partnership folder was a misread of 2024 return — removed
- 2026-03-28: Auburn CA is a passive RE investment waiting on K-1 (reference 2024 return for context)
- 2026-03-28: No estimated tax payment docs for 2025; filing extensions in 2026
- 2026-03-28: Questionnaire exercise proved ~97% coverage achievable with 6 user questions + autonomous portal checks
- 2026-03-28: Check-yourself-first principle — CHITRA should attempt portal/site checks before asking the user
- 2026-03-28: Employer HR portals are user-provides — too much SSO friction to automate
- 2026-03-28: Gmail skill is high priority — CPA correspondence and charitable docs both live in email
- 2026-03-28: Portal credential registry uses Keychain for secrets, portals.yaml.template for portal metadata (URLs, auth methods, doc types)

- 2026-03-28: Slack Socket Mode (push) preferred over polling for OTP — instant delivery, no API quota waste
- 2026-03-28: MFA/OTP notifications MUST go via Slack DM, never rely on IDE messages (user may not be at computer)
- 2026-03-28: PortalSession class handles credential → login → OTP → download → upload → registry update lifecycle
- 2026-03-28: Portal navigation scripts are CHITRA-readable instructions, not standalone executables
- 2026-03-28: Schwab login works WITHOUT MFA; E*Trade always requires SMS MFA
- 2026-03-28: When portal offers email-based OTP, prefer it (future Gmail skill can read autonomously)
- 2026-03-28: Portal navigation modules are structured PORTAL_CONFIG dicts — not prose docstrings, not executable scripts
- 2026-03-28: Navigation knowledge (how to use Schwab) is checked in like DB drivers; user's portal manifest (which portals they use) is gitignored
- 2026-03-28: portals.yaml.template sanitized to generic examples — user-specific portal list lives in portals.yaml (gitignored)
- 2026-03-28: Answer-processing pipeline maps life events to multi-document expansions (e.g. "new home" → 4 docs)
- 2026-03-28: Credential collection is conversational via Slack DM (ask username, then password), stored in Keychain, messages deleted from chat after storage
- 2026-03-28: TaskRunner orchestrates the full loop: task list → cred check → Slack ask → plan gen → AI execution → status notify
- 2026-04-05: Collaborative browser model: AI navigates, user enters creds in visible browser, AI captures via JS interceptor + stores in Keychain
- 2026-04-05: JS credential interceptor must store in sessionStorage/localStorage to survive page redirects (window variables are destroyed)
- 2026-04-05: SAML login flows (Wells Fargo) cross origins, wiping even localStorage — direct form field polling via Playwright is more reliable
- 2026-04-05: Chrome CSV export → Keychain bulk import is the most efficient credential collection method (Google has no API for Password Manager)
- 2026-04-05: Collaborative browser model is still the right approach for: first-time portal logins without saved passwords, stuck navigation, CAPTCHA handling, MFA flows
- 2026-04-05: Learnings directory (`agents/chitra/knowledge-base/learnings/`) stores per-portal navigation patterns from collaborative sessions

## Git State
- Branch: `main`
- Remote: configured (private SSH key)
- Public URL: https://github.com/aditya2kx/jarvis
