# Jarvis Build Progress

## 2026-06-09 — Grafana hotfix: KDS query-var, Min/Item threshold, labor y-axis cap

**Context:** After PR #43 merged, the operator found three dashboard gaps. This hotfix (branch `fix/grafana-kds-vars-labor-yaxis`, off `main`) fixes them; the dashboard bumps to v28.

1. **`KDS: Order Date` showed a query error** (`Error 400: Required parameter is missing: query`). PR #43 stored the `kds_date` query variable as a bare SQL string; the BigQuery datasource plugin needs the structured query object (`rawSql` inside a `query` object with `project`/`dataset`). Restructured it and set `refresh: 1` (on dashboard load — the date-list query has no `$__timeFilter`). Verified via `/api/ds/query`: the structured `rawSql` returns 46 date rows.
2. **Min/Item threshold appeared stuck at 8.** Root cause was the broken `kds_date` variable leaving panel 52 in a stale state (dependent panel never re-queried). Also switched panel 52's threshold from `CAST('$kds_min_per_item' AS FLOAT64)` to the idiomatic unquoted numeric `>= $kds_min_per_item` per the BQ plugin docs. Verified threshold `5` returns rows with Min/Item 5–7. Updated the panel description to tell operators to press Enter after editing the threshold and to clear any in-table column filter.
3. **Daily Labor Wages / Net Sales y-axis** now capped at 100% (`min: 0, max: 1` on panel 32, matching the Hours/Item panel).

**Also:** `bind_datasource_uid` now rewrites query-type template variables' own `datasource.uid` (was only panels/targets), so `kds_date` resolves to the real UID at deploy. New regression test in `test_deploy_bind_uid.py`.

**Verification:** `verify_panels.py` → OK=11 EMPTY=0 ERROR=0; `test_deploy_bind_uid.py` (6) + `TestGrafanaContractInSync` (2) pass. **Live evidence:** deployed the branch dashboard to Grafana Cloud via `deploy.py --dashboard-only` (a dashboard is a review surface; the repo stays source of truth and the next merge re-syncs). Confirmed live `version: 28`, `kds_date.query` is the structured object with `refresh: 1` + bound datasource UID, panel 32 `max: 1`, panel 52 threshold `>= $kds_min_per_item` (unquoted). Link: https://steadyangelfish2985.grafana.net/d/bhaga-analytics-v1/bhaga-analytics

**Process:** documented the "deploy the dashboard from the branch, the live link is the evidence" workflow in `CONTRIBUTING.md` (Additive prod data-source exception → Grafana dashboard changes), so every future Grafana PR provides a live-link + confirmed-version as §4 evidence rather than only a `verify_panels.py` SQL check.

## 2026-06-09 — PR B: BQ-authoritative Labor Forecast + Grafana Section 7

**Scope:** PR B (branches off `main`; separate from PR A "KDS Dashboard tweaks + CI policy").

**Changes landed (pending merge):**
- **`forecast_bq.py`** — new BQ-authoritative 30-day forecast: reuses pure `forecast.py` functions, outputs `{date, forecast_orders, forecast_items, forecast_generated_at}` rows. Horizon configurable via `forecast_horizon_days` store profile key (default 30).
- **`materialize_model_bq.py`** — integrated forecast load after `model_labor_daily` write. Merge key: `date`. Future window only; past rows freeze for implicit accuracy tracking. Skip via `BHAGA_SKIP_FORECAST=1`. Non-fatal.
- **`update_model_sheet.py`** — removed `labor_daily_forecast` Sheet tab write (retired).
- **`core/migrations/011_labor_forecast.sql`** — new idempotent migration: `model_forecast_daily` table + `vw_model_forecast`, `vw_forecast_accuracy`, `vw_forecast_exclusions` views.
- **`agents/bhaga/knowledge-base/store-profiles/palmetto.json`** — removed `labor_daily_forecast` tab; added `forecast_horizon_days: 30`.
- **`agents/bhaga/grafana/dashboard.json`** v29 — new Section 7 "Labor Forecast" (panels 71-73): forecast table, forecast-vs-actual timeseries, exclusions table; built on top of v28 hotfix changes.
- **`agents/bhaga/scripts/backfill_bigquery.py`** — added `map_forecast_daily` mapper.
- **`test_forecast_bq.py`** — 9 new unit tests (all pass).
- **Docs:** RUNBOOK §15, agents/bhaga/scripts/README.md, DOMAIN.md §7 updated.

**ADP scheduled hours (Part 4):** dropped — Keychain credential `adp_palmetto_login` not found in this environment. Deferred to a follow-up PR on a machine with Keychain configured.

**Additional fix:** `MODEL_VERIFY_MIN_ROWS` in `daily_refresh.py` and `PROD_RAW_VERIFY_MIN_ROWS` / `SANDBOX_E2E_VERIFY_MIN_ROWS` in `sandbox_e2e.py` updated to remove `labor_daily_forecast` (sandbox e2e was failing with "labor_daily_forecast: 0 row(s) expected >= 1").

**Migration required after merge:** `python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"` then trigger a manual refresh (RUNBOOK §6) to populate `model_forecast_daily`. `status.py` GRAFANA_VIEWS registry updated to include the three forecast views (dashboard v29); anti-drift coupling through v29.

## 2026-06-09 — Grafana dashboard: KDS defaults, p99 goal line, goal-var grouping (PR A)

**Changes:** PR A (dashboard tweaks + CI sandbox policy).

Dashboard changes (`agents/bhaga/grafana/dashboard.json`, version 26→27):
- **Order KDS Times (panel 52):** defaults to the most-recent order date (`kds_date` query var) and Min/Item ≥ 8 (`kds_min_per_item` textbox), both adjustable via top-of-dashboard dropdowns. SQL now filters `date_local = '$kds_date'` and `ROUND(order_min/num_items,1) >= CAST('$kds_min_per_item' AS FLOAT64)`.
- **KDS Time per Item (panel 51):** added a dashed `p99 Goal` baseline series using new `goal_kds_p99_min` textbox var (default 8 min).
- **Template variables:** reordered so filters (`date_from`, `kds_date`, `kds_min_per_item`) come first, then goals (`goal_hours_per_item`, `goal_labor_pct_of_net_sales`, `goal_kds_p99_min`) grouped left-to-right. Each variable has a `description`. `goal_hours_per_item` changed from 0.15 → 0.20 (20%).
- `verify_panels.py._template_defaults` extended to also resolve `query`-type vars so `$kds_date` substitutes during local verification.

CI sandbox policy changes: the default per-PR `Sandbox e2e` gate is made opt-in (label/dispatch); targeted scenarios run per-plan via Tier-2. Required-check removed from ruleset "Protect Master".

**Verification:** `verify_panels.py` shows all 11 panels OK, including panel 52 returning 5 rows for 2026-06-08 with Min/Item ≥ 8 filter, and panel 51 with the new `p99 Goal` series. `TestGrafanaContractInSync` passes (no new views added).

## 2026-06-09 — OTP-recovery invalidation widened to model-render steps (PR #TBD on branch fix/recovery-invalidate-model-steps)

**Incident (2026-06-08 prod recovery, post PR #41 deploy):** After the login fix merged + deployed, I re-ran prod for 6/8. Login **recovered** (blank magic link → discarded session → fresh retry → operator OTP + a deliverable magic link → dashboard) and 108 Square transactions / 146 item lines landed in BQ — but the run then **failed the post-condition guard**: `data_window_end` stayed at 2026-06-07. Root cause: the OTP-recovery marker-invalidation list `_RECOVERY_DOWNSTREAM_STEPS` only cleared `load_raw_bigquery` / `update_model_sheet` / `process_reviews`. The earlier partial run had already marked `render_raw_sheets` + `materialize_model_bq` done, so they stayed skipped — the fresh Square rows reached BQ raw but were never re-projected into Sheet raw, `update_model_sheet` (legacy path) computed from stale Sheet raw, and the window stuck. (The guard did its job — it caught the silent partial success — but only after a wasted run.)

**Recovery (no extra OTP):** the Square data was already in BQ, so I cleared only the stale model/projection markers (`render_raw_sheets`, `update_model_sheet`, `materialize_model_bq`, `process_reviews`) for `runs/2026-06-08`, leaving `square_transactions` / `load_raw_bigquery` done, and re-ran. Square was skipped (no OTP); `render_raw_sheets` re-projected 6/8, `materialize_model_bq` recomputed, `update_model_sheet` advanced **`data_window_end` → 2026-06-08**, held-back reviews released, guard passed, run exited 0. Verified independently: `bhaga.square_item_lines`=146, `bhaga.square_transactions`=108 for 2026-06-08.

**Fix:** `_RECOVERY_DOWNSTREAM_STEPS` now lists **every** step that carries portal data to the window, in pipeline order: `load_raw_bigquery` → `render_raw_sheets` → `update_model_sheet` → `materialize_model_bq` → `render_model_sheet_from_bq` → `process_reviews`. So a future OTP recovery advances the window in **one** run. Regression test binds the test's `DOWNSTREAM` to the production constant (can't drift) + asserts the render/materialize members are present and cleared. Docs updated (RUNBOOK §13, scripts README, bhaga-principles).

**Files changed:** `agents/bhaga/scripts/daily_refresh.py`, `agents/bhaga/scripts/test_daily_refresh.py`, `RUNBOOK.md`, `agents/bhaga/scripts/README.md`, `.cursor/rules/bhaga-principles.md`, `PROGRESS.md`.

## 2026-06-09 — Square login resilience: recover from anti-bot blank magic-link block (free, no laptop) (PR #TBD on branch fix/square-login-resilience)

**Incident (2026-06-08 nightly):** Square escalated the headless Cloud Run container as an "unrecognized device" and served the "Magic link sent" screen **with a blank recipient and sent no email** (`.magic-link-sent__email` empty; "we sent a magic link to ."). The old code DM'd the operator to paste a magic-link URL that never arrives; the operator replied "haven't gotten one" and the Square step failed. ADP + reviews ran, but all Square data (sales/tips/items/KDS) was missing for 6/8 (`data_window_end` stuck at 6/7). Root cause = ThreatMetrix + Cloudflare bot fingerprinting on a rotating Cloud Run egress IP — the persisted `TRUSTED_person` cookie was ignored.

**Change (free, laptop-independent):**
- `skills/square_tips/runner.py`: `_magic_link_recipient()` classifies the magic-link screen; a **blank recipient** raises new `SquareDeviceBlockedError` **before** any Slack paste prompt. `_drive_verification()` (extracted from `_ensure_logged_in`, now `attempt`-aware) catches the block on attempt 1, calls `gcs_cache.delete_session()` to discard the poisoned session, and raises `_RetryFreshLogin`.
- `agents/bhaga/scripts/gcs_cache.py`: `delete_session()` (idempotent, never raises).
- `agents/bhaga/scripts/daily_refresh.py`: `_run_square_session_with_retry()` retries the Square session **exactly once** with `storage_state=None` (fresh cookie jar → often re-presents SMS-OTP, answered on the phone via Slack); `_is_square_device_block()` routes the failure to the new actionable alert.
- `agents/bhaga/notify.py`: `square_device_blocked_alert()` — tells the operator there is **nothing to paste** and the next nightly auto-retries on a fresh egress IP (no dead-end magic-link prompt).

**Safe by construction (no feature flag):** the first attempt fires **no** SMS, so the single fresh retry can never duplicate one; bounded to one retry (a second block propagates); downstream writes stay idempotent and the §13 partial-failure recovery releases the held-back Square data once a later run succeeds.

**Live sandbox proof (PR #41, item-sales-live, 2026-06-08):** A Tier-2 live run against the PR image reproduced the block AND proved recovery end-to-end. Cloud Run trace frames + logs: attempt 1 hit the blank-recipient magic link → `_magic_link_recipient` raised `SquareDeviceBlockedError`, `delete_session` discarded the poisoned session, `_RetryFreshLogin` fired; attempt 2 (fresh cookie jar) → SMS-OTP (answered in Slack) → a *deliverable* magic link → `/dashboard`; transactions + item-sales + KDS then downloaded, landing **146 item-sales rows for 2026-06-08 in `bhaga_sandbox.square_item_lines`**. This exercised both new branches (blank → recover; deliverable → existing relay) in one run.

**Also fixed (verify harness):** `sandbox_live_run.verify_item_sales` was checking a deprecated GCS path (`<date>/square/items-*.csv`) the nightly no longer writes (BQ is the source of truth), so it failed every item-sales-live run regardless of the scrape. Rewritten to assert the BQ row count in `<dataset>.square_item_lines` and never consult GCS.

**Deferred (not free):** pinning a static egress IP (Serverless VPC connector + Cloud NAT + reserved IP, ~$30-45/mo) would stop the escalation at the source. Out of scope per the "free, no laptop" constraint.

**Files changed:** `skills/square_tips/runner.py`, `skills/square_tips/test_runner_magic_link.py`, `agents/bhaga/scripts/gcs_cache.py`, `agents/bhaga/scripts/test_gcs_cache.py`, `agents/bhaga/scripts/daily_refresh.py`, `agents/bhaga/scripts/test_parallel_refresh.py`, `agents/bhaga/notify.py`, `agents/bhaga/test_notify.py`, `RUNBOOK.md`, `.cursor/rules/bhaga.md`, `agents/bhaga/scripts/README.md`, `PROGRESS.md`.

## 2026-06-08 — Cost framework: multi-model attribution fix + post-merge self-heal (PR #40)

**Change:** Fixed two structural gaps in the per-PR cost ledger surfaced by PR #39.

**Gap 1 — multi-model attribution (PR #39 recorded $6.94 / 100% Sonnet; correct is $12.08 with Opus):**
`filter_events_for_conversations` in `scripts/cursor_usage.py` gated event inclusion on the conversation's "dominant model" — `max(models, key=len)`, an arbitrary string-length comparison. In a plan-in-Opus / execute-in-Sonnet session, Sonnet (17 chars) beats Opus (15 chars), so all Opus events were silently dropped. Fixed by checking the event's tier against the conversation's full model SET (`_model_in_conversation`). PR #39 recomputed: Opus-4.8-medium $2.46 + Opus-4.8-high $2.68 + Sonnet $6.94 = **$12.08** total.

**Gap 2 — post-merge `merged_at`/report self-heal:**
`pr-cost-finalize.yml` computes the correct post-merge values but the repo ruleset blocks its push to `main`. Added `scripts/git-hooks/post-merge`: fires after `git pull` on `main`, runs `pr_cost_ledger.py report`, backfills `merged_at` for all merged PRs locally, and regenerates `report.html` — so the local report is correct immediately after pulling (no more stale report). Added `scripts/finalize_cost.sh <pr>` for on-demand immediate finalization via a metrics-only PR. Updated `pr-workflow.mdc` step 7 and `CONTRIBUTING.md` to document the eventual-consistency model and these tools.

## 2026-06-08 — Google review bonus: $20 pool split (effective 2026-06-08, PR #TBD on branch feat/review-bonus-jun8)

**Change:** Replaced the per-person review bonus structure with a fixed **$20-per-review pool**
split equally among in-hours part-time staff, effective for reviews posted on/after 2026-06-08.

**Key decisions:**
- Date-bracketed on `post_date_ct`: reviews before 2026-06-08 keep the legacy $10-base / $20-named-shoutout per-person rules (proven byte-identical by `AllocateBonusLegacyRegressionTests`).
- Pool requires `assignment_reason == "in_hours"`; no last-shift fallback.
- Permanent + training exclusions apply to pool; shoutouts ignored (named person gets the same share).
- Pool split to the cent (integer cents via `divmod`); remainder to alphabetically-first members.
- No BQ schema migration — pool shares roll into existing `base_dollars`/`total_bonus` columns.
- No feature flag — output is human-read payroll prep; BHAGA never auto-writes ADP.

**Files changed:** `agents/bhaga/scripts/process_reviews.py`, `agents/bhaga/scripts/update_model_sheet.py`, `agents/bhaga/scripts/test_process_reviews.py`, `agents/bhaga/knowledge-base/DOMAIN.md`, `.cursor/rules/bhaga.md`, `agents/bhaga/scripts/README.md`.

## 2026-06-07 — Grafana "No data" fix: deploy-time datasource-UID binding + BQ panel aliases (PR #38)

**Goal:** Every panel on the BHAGA Analytics dashboard showed "No data". Root-cause and fix.

**Two independent bugs:**
- **Datasource wiring (global):** the `ds_bigquery` template variable stored the datasource *name*
  (`"BHAGA BigQuery"`) while every panel references `"uid": "${ds_bigquery}"` → Grafana resolves a
  datasource whose UID == the name → "Data source not found" → all panels blank.
- **Invalid SQL:** the 11 timeseries panels used double-quoted aliases (`AS "Orders"`) — a string
  literal in BigQuery Standard SQL → syntax error. Output field names also can't contain `/` or `$`.

**What landed:**
- `skills/grafana_cloud_provisioning/register.py` — `configure_bigquery_datasource` now returns the
  datasource `uid`; new `get_bigquery_datasource_uid()` helper.
- `agents/bhaga/grafana/deploy.py` — `bind_datasource_uid()` rewrites every `${ds_bigquery}` ref + the
  var `current` to the real UID before push; `dashboard.json` stays UID-free. `--dashboard-only` looks
  up the UID too. Fails loudly if the UID can't be resolved.
- `agents/bhaga/grafana/dashboard.json` — backtick aliases; `Hrs / $1k Net Sales`/`Hrs / Item` →
  `Hrs per 1k Net Sales`/`Hrs per Item`; `byName` field overrides kept in sync.
- `agents/bhaga/grafana/verify_panels.py` — read-only per-panel harness via Grafana `/api/ds/query`;
  **it caught the alias bug** that earlier ad-hoc testing had masked.
- Docs: RUNBOOK §14 (deploy-time UID binding + alias contract + incident); `status.py` panel-SQL
  contract note. Deployed to prod; `verify_panels.py` → **14/14 panels OK**.
- **Deferred (Phase 3, operator-driven):** `$inv_date` default, pre-window flat-zero forecast rows,
  `time.from` vs `$date_from` alignment — cosmetic/UX, not blockers.
- **Operator-feedback refinements (same PR #38):** (1) labor ratio panels renamed off "$1k net sales"
  with goal **baseline lines** on the total daily/weekly panels, driven by new `$goal_hours_per_net_sales`
  / `$goal_hours_per_item` template vars; (2) KDS "Time per Item" panel y-axis capped at 30 min;
  (3) migration `008_kds_order_grouping.sql` adds `ticket_name` (order id) + `order_source` to
  `vw_kds_item_investigation`, and the slow-items table is restructured to group items by order
  (honest note: KDS times are per-order, not per-item).
- **Operator-feedback round 2 (same PR #38):** (1) labor/payroll hour fields now use the Grafana
  `suffix: h` custom unit so values render in **hours** (the built-in `h` unit auto-scales to days/min —
  that was the "shows days" bug); (2) dropped "$1K" from the net-sales series labels; (3) split Labor
  into **3. Daily Labor** + **4. Weekly Labor** sections (Order Quality→5, Payroll→6); (4) **Slow Orders**
  is now order-level via migration `009_kds_order_level.sql` (`vw_kds_order_investigation`) — one row per
  ticket with start/end time and the full item list, flagged when `order_min > items × $max_item_min`;
  (5) payroll table relabeled into consistent **Calculated / ADP / Diff** triads (Hourly Pay, Tip Pay,
  Review Bonus, Total Pay) with a new `Diff Total Pay` column and a description explaining each.
  June-7 "missing data" was a refresh/cache artifact — all source views (labor/order-quality/KDS) have
  June 7 and the `now-90d..now` window includes it.
- **Operator-feedback round 3 (same PR #38):** the labor-vs-sales metric became raw `total_hours ÷
  net_sales` (no $1,000 scaling); all "$1K" wording removed (incl. the goal variable label); decimals
  bumped to 3; non-positive-net-sales days blanked so anomaly days don't blow up the axis. Panel titles
  use explicit `${var}` interpolation so the date/threshold render. Migration `010_kds_order_quality.sql`
  added `vw_kds_order_quality_daily` (order-level percentiles) — but the operator then chose to keep the
  KDS percentile chart at **per-item** level (`vw_order_quality_daily`, y-cap 30 min), so that order-level
  view is currently unused by the dashboard (kept in BQ; harmless). The Slow Orders table stays order-level.
- **Operator-feedback round 4 (same PR #38):** labor ratios split into **two charts per period** instead of
  one dual-axis chart per cohort. Each period (Daily/Weekly) now has: (1) **Hours / Net Sales** with three
  lines — total / part-time / full-time — and (2) **Hours / Item** with the same three lines; each chart
  carries a single dashed **Goal** line (`$goal_hours_per_net_sales` / `$goal_hours_per_item`). Because each
  chart is now a single metric type, the lines share one axis (no dual-axis cramming). The per-cohort panels
  34/38 were removed and the Shift-Hours charts widened to full width. No view changes (still
  `vw_model_labor_daily` / `vw_model_labor_weekly`), so the `status.py` registry is unchanged.
- **Operator-feedback round 5 (same PR #38):** (1) **Weekly Order & Item Volume** is now a `barchart` whose
  x-axis is an explicit week-range label (`CONCAT(FORMAT_DATE start, ' – ', FORMAT_DATE start+6d)`, e.g.
  "Jun 1 – Jun 7") so each bar visibly = one full Mon–Sun period; Items Sold on the right axis. (2) **Daily
  Hours / Item** y-axis capped at `0–1.0`. (3) Grafana template variables are dashboard-scoped (always in the
  top bar) and can't be moved into a single panel — but the three **table** panels (Payroll, Slow Orders, Who
  Worked) now have `custom.filterable: true`, giving native in-panel column filters (filter by Employee /
  Period / Source right in the table) instead of relying solely on top-bar vars. No view changes.
- **Operator-feedback round 6 (same PR #38):** (1) **Weekly Order & Item Volume** reverted from `barchart`
  back to a `timeseries` bar+line combo (Orders = solid bars left, Items Sold = line right) to de-clutter.
  **Grafana constraint learned:** a `barchart` (the only panel with category x-axis labels like "06/01 –
  06/07") can't draw a line series, and `timeseries` (the only bar+line combo) can't show a start–end range
  as x-axis tick labels — so with bar+line + the labor goal lines, all weekly charts stay `timeseries` with a
  weekly time axis (each tick = week start; full week in the tooltip). (2) **Daily Hours / Item** y-axis
  capped at 0–1.0. (3) Removed the `inv_date` and `max_item_min` top-bar variables now that the tables filter
  in-panel: **Slow Orders** uses a fixed 8 min/item threshold, gained a filterable **Date** column, and is
  bounded by `$date_from`; **Staff on Shift** (was "Who Worked That Shift") likewise gained a Date column and
  `$date_from` bound. (4) All bar series (daily + weekly Orders) set to solid fill (`fillOpacity 100`,
  `gradientMode none`). No BQ view changes, so `status.py` GRAFANA_VIEWS is unchanged.
- **Operator-feedback round 7 (same PR #38):** weekly x-axis week labels + configurable slow threshold.
  **Confirmed Grafana limit (instance is v13.1):** a literal date *range* tick label ("6/1-6/7") needs a
  category x-axis, which only the `barchart` panel has; `timeseries` (lines / bar+line) has a time axis whose
  ticks are single instants (formattable to e.g. "6/1" but not a range). So per operator choice we went
  **hybrid**: (a) **Weekly Order & Item Volume** is a `barchart` whose x label is the numeric range
  `CONCAT(M/D, '-', M/D+6d)` → "6/1-6/7"; (b) the weekly **line** charts (Shift Hours, Hours/Net Sales,
  Hours/Item) keep their lines, format the x-axis time field as `time:M/D` (→ "6/1"), and carry a
  tooltip-only `Week` string field (hidden from legend/viz via `custom.hideFrom`) so hovering shows the full
  "6/1-6/7" range. **Slow threshold reinstated as a `custom` dropdown** `max_item_min` (5–15, default 8): the
  Slow Orders table now shows `Min / Item` (actual) and `Threshold (min/item)` columns, computes Expected Min
  = Items × threshold, flags `order_min > items × threshold`, and the title/description interpolate
  `${max_item_min}`. `verify_panels._template_defaults` extended to substitute `custom` vars (not just
  `textbox`) so the harness mirrors Grafana. No BQ view changes.
- **Operator-feedback round 8 (same PR #38):** fixed a regression + flexibility. (1) **Bug:** the weekly
  **line** charts rendered as dots, not lines — the tooltip-only `Week` *string* column from round 7, in a
  BigQuery **time-series-format** query, is treated as a **pivot dimension**, exploding each metric into
  one-point-per-week series. Removed the `Week` column + its override from panels 35/36/37; they're plain
  lines again (kept the `time:M/D` x-axis format → "6/1" ticks). **Lesson:** never add a non-time string
  column to a `format:0` (time series) BigQuery target — it pivots. (2) Weekly Order & Item **bar value
  labels** enlarged (`options.text.valueSize: 16`, `showValue: always`). (3) **Order KDS Times** (was "Slow
  Orders"): the query no longer pre-filters to slow/one-date — it returns every order in the From-Date window;
  added a filterable **Slow?** (Yes/No) column computed from the `max_item_min` dropdown plus **Min / Item**
  and **Threshold** columns, so the operator filters Date and Slow? **in-table** and changes the threshold via
  the dropdown without touching the underlying data. Title dropped the hardcoded "8 min". No BQ view changes.
- **Operator-feedback round 9 (same PR #38):** the threshold control was confusing — the per-row "Threshold"
  column was a constant (= the dropdown's current value), so its in-table filter only listed "8". Reworked so
  the **`Slow threshold (min/item)` dropdown** (now 5/6/7/8/9/10/12/15/20) directly drives the **Slow Orders**
  table: the query filters `order_min > num_items × ${max_item_min}`, so picking 5 vs 15 shows all orders over
  that per-item time (verified 2071 vs 203 rows). Removed the constant Threshold + Slow? columns; the table is
  now Date (filterable) / Order / Source / Start / End / Items / Order Min / Min per Item / Items in Order,
  sorted by Min/Item desc. No BQ view changes.
- **Operator-feedback round 10 (same PR #38):** dropped the threshold dropdown entirely. Grafana 13's table
  **column filter supports numeric comparators** (≥ / ≤), so the right UX is: the query pre-filters nothing
  (every order in the From-Date window), and the operator sets their own slow threshold in-table via the
  **Min / Item** column filter (e.g. ≥ 10). Removed the `max_item_min` variable; Slow Orders query is now just
  `WHERE date_local >= '$date_from'` sorted by Min/Item desc (4232 rows, filtered client-side). This also
  fixed the "No values" trap where the dropdown's `> 8` pre-filter hid everything ≤ 8. No BQ view changes.

## 2026-06-06 — GCS out of the data pipeline + fresh-scrape TRUNCATE-then-load (PR #33)

**Goal:** Make the implementation match the PR's stated design — *BQ is the single source of truth;
GCS = sessions/evidence only* — and unblock the fresh-scrape sandbox backfill (which was failing the
`load_raw_bigquery` step on `MERGE must match at most one source row for each target row` when a single
ADP earnings scrape batch carried duplicate natural keys).

**What landed:**
- `daily_refresh.py` — removed ALL scrape-data uploads to GCS (`upload_scrape_artifacts` ×2 +
  `_cache_artifact_now`). Scrape exports are parsed straight into BQ by `load_raw_bigquery`; GCS now
  holds only browser sessions + failure evidence. Added an explicit **DATA ARCHITECTURE** docstring
  (scrape → transient local file → BQ; never GCS) so future agents don't reintroduce the retired
  scrape→GCS→BQ-mirror path. Dropped now-unused `upload_file`/`upload_scrape_artifacts` imports.
- `gcs_cache.py` — module + function docstrings narrowed to "sessions + evidence only"; the data-file
  helpers (`upload_file`, `upload_scrape_artifacts`, `download_cached_files`) flagged **LEGACY**
  (offline backfill + `sandbox_e2e` replay only; not the live pipeline).
- `core/datastore.py` — `load_rows(..., replace=True)` = TRUNCATE-then-INSERT for a fresh full-history
  scrape (the scrape owns the whole table; sidesteps the MERGE one-source-row error on duplicate keys).
  `_insert_rows` is now hint-aware (all-None columns type correctly) and used by every non-merge load.
- `backfill_from_downloads.py` — `--replace` flag (defaults on when `BHAGA_RAW_REPLACE=1`); a module
  `load_rows` wrapper injects `replace=True` across all ~10 call sites.
- `sandbox_live_run.py` — fresh-scrape path also sets `BHAGA_RAW_REPLACE=1`; `--sheet-from-bq`
  (`BHAGA_SHEET_FROM_BQ=1`) so the sandbox runs the BQ-canonical model path
  (`materialize_model_bq` → `render_model_sheet_from_bq`) instead of the legacy raw-Sheet-reading
  `update_model_sheet`. `full-history-bq-sandbox` scenario enables both.
- Tests: `core/test_datastore_dataset_isolation.py` (replace truncate-then-insert, dup-key keep,
  merge-path unaffected), `test_backfill_from_downloads_replace.py` (wrapper plumbing),
  `test_sandbox_live_run.py`/`test_sandbox_scenarios.py` (sheet_from_bq). Full suite green (869 passed).

## 2026-06-06 — Sandbox BQ dataset isolation + scrape-from-source evidence (PR #33)

**Goal:** Produce trustworthy parity evidence (BQ raw/model vs prod Sheets, from 2026-03-23) WITHOUT
letting PR-branch code touch prod data. Discovered the sandbox isolated sheets/cache/Firestore but
**not** BQ — sandbox runs wrote the shared prod `bhaga` dataset (the path that leaked a test row into
prod `model_review_bonus_period`).

**What landed:**
- `core/datastore.py` — BQ dataset is now env-driven (`BHAGA_BQ_DATASET`, default `bhaga`):
  `dataset()` / `fq(table)` helpers, `ensure_dataset()` (create-if-missing), `ensure_schema()` rewrites
  migration DDL to the active dataset, and `_assert_sandbox_write_isolation()` blocks a
  staging run from writing the prod dataset. Used by `load_rows` and `load_model_rows` (replace path).
- Repointed hardcoded dataset literals to the env-driven dataset: `render_raw_sheet_from_bq`,
  `render_model_sheet_from_bq`, `reconcile_model`, `status`, `bq_coverage`, `process_reviews`,
  `update_model_sheet`, `core/store_config`, `cloud/webhook/handler`.
- `sandbox_live_run.py` — sandbox env overlay sets `BHAGA_BQ_DATASET=bhaga_sandbox` + isolation
  assertion; new `--fresh-scrape` flag points the cache READ bucket at the empty sandbox bucket so a
  windowed backfill must hit the **actual upstream sources** (not prod GCS cache / not Sheets).
- `materialize_model_bq.py` — `load_model_rows(replace=True)` now also runs the sandbox write guard;
  item-metrics (`items_sold`/KDS) now computed from BQ via `read_item_daily_bq`/`read_kds_daily_bq`.
- `agents/bhaga/scripts/verify_prod_parity.py` (new) — cloud-runnable e2e parity tool: BQ raw/model row
  counts + key-joined, unit-aware value diffs vs prod Sheets; dataset is env-driven so it can verify
  either prod or `bhaga_sandbox`.
- Created the `bhaga_sandbox` BQ dataset and ran migrations 001–007 into it (20 tables + 13 views).
- Tests: `core/test_datastore_dataset_isolation.py`, `core/test_datastore_reader.py`, sandbox isolation
  + fresh-scrape cases. Full suite green (859+ passed).

## 2026-06-05 — BQ as single source of truth (PR #33, feat/grafana-dashboard-refactor)

**Goal:** Make BigQuery the single source of truth for all BHAGA data (raw scrapes, ADP earnings,
operator tunables). Retire GCS as a data source (keep sessions + evidence only). Replace the
sheet-based gap-resolver with BQ coverage. Add `/bhaga-cloud config set/get` Slack commands.

**What landed:**
- `agents/bhaga/scripts/bq_coverage.py` — `present_days` / `missing_ranges` / `SOURCE_COVERAGE`; 11
  unit tests.
- `core/migrations/007_store_config.sql` — `bhaga.store_config` table for operator tunables.
- `core/store_config.py` — `get_config` / `get_all` / `set_config` over `core.datastore`.
- `agents/bhaga/scripts/update_model_sheet.py`:
  - `load_cc_tips_earnings_from_bq` — reads `bhaga.adp_earnings`, returns ISO-string date keys (hard
    cutover; GCS XLSX path retired as live source).
  - `_read_config_value` — BQ-first (`core.store_config.get_config`), Sheet fallback.
  - `period_has_cc_tip_actuals` — repointed to `load_cc_tips_earnings_from_bq`.
  - main() earnings call repointed to BQ.
- `agents/bhaga/scripts/materialize_model_bq.py` — earnings call repointed to `load_cc_tips_earnings_from_bq`.
- `agents/bhaga/scripts/verify_bq_parity.py` — earnings call repointed to BQ; XLSX fallback removed.
- `agents/bhaga/scripts/daily_refresh.py`:
  - Gap-resolver replaced: `bq_coverage.missing_ranges` → `gap_start = earliest_missing_day` (BQ
    path); sheet-based fallback when BQ unavailable.
  - `download_cached_files` skip-scrape role removed (both pre-scrape and post-parallel calls).
  - `load_raw_bigquery` failure clears `square.done`/`adp.done` markers (retry-skips-rescrape).
- `cloud/webhook/handler.py` — `/bhaga-cloud config get <key>` and `/bhaga-cloud config set <key> <value>`
  using `google.cloud.bigquery` directly (standalone deploy unit constraint).
- `cloud/webhook/requirements.txt` — added `google-cloud-bigquery>=3.0,<4`.
- Tests: `test_bq_coverage.py` (11), `test_bq_sot.py` (7), `core/test_store_config.py` (6),
  `cloud/webhook/test_handler.py` (5 new); 509 total passing.
- Docs: RUNBOOK §1 + §15, README pipeline description, DOMAIN adp_paid, bhaga.md data flow +
  invariants, bhaga-principles.md BQ SoT rules + plan-execution-readiness pointer.

**Next:** apply migration 007, seed `bhaga.store_config`, run OTP-supported prod backfill to fill
`adp_earnings` gaps, verify Grafana `adp_paid`/`diff` populated.

## 2026-06-04 — Cost ledger via pre-commit hook; PR cost gate is now a pure validator (feat/cost-ledger-precommit-hook)

Reverted the commit-back approach (below) — it was the root cause of duplicate CI and churn. The
commit-back pushed a second `chore(cost):` commit per push, which forced a bad trade-off:
`GITHUB_TOKEN` push → no CI on the cost commit → auto-merge blocked; `ADMIN_PAT` push → CI fires but
`cancel-in-progress: true` kills the in-flight run on the real commit and starts a second round on the
cost commit → every required check shows up twice and the "real" CI runs on a bot commit. The cost
script's own docstring already documented the correct design: *"the operator commits the complete
record once"* — not a bot pushing from CI.

**Fix:**
- **`scripts/git-hooks/pre-commit`** (new) — runs `pr_cost_ledger.py sync` and **auto-stages**
  `metrics/pr_cost/` into the author's own commit, so the ledger + `report.html` land on `main` in the
  squash merge with no CI push-back. Never blocks; no-op until the PR exists. Replaces the old
  block-and-retry `pre-push` hook (removed).
- **`pr-cost-gate.yml`** — stripped to a pure validator (`validate --require-build`); no
  `contents: write`, no PAT, no commit-back, no identity/fetch hacks.
- **`sandbox-e2e.yml` / `claude-review.yml`** — removed the `chore(cost):` skip steps; CI runs fully
  on every push because there are no more automatic cost commits to skip.
- **`pr-cost-finalize.yml`** — unchanged (post-merge analysis comment).
- **`pr-workflow.mdc`** — added: check for open PRs before creating a new one; install the cost hook;
  post-merge `git pull` + artifact spot-check (the main working copy was left 2 PRs stale after #32/#34).
- Tradeoff (inherent to any design): a push's own review cost can't be in the commit that triggered
  it; it's captured by the next commit's sync or finalized at merge.

## 2026-06-04 — Cost ledger commit-back on every push (feat/cost-commit-on-push)

Fixed the broken `pr-cost-finalize.yml` post-merge push. Root cause: the repo ruleset
(`enforcement: active`, `bypass_actors: []`) blocks all direct pushes to `main`, including from
`ADMIN_PAT`. The workflow silently swallowed the error (exit 0) so CI showed green but the
`report.html` was never committed.

**Fix:** `pr-cost-gate.yml` now commits the refreshed ledger (`capture-review` + `report.html`)
back to the **PR branch** after each validation pass (every push). The cost commit (prefix
`chore(cost):`) is the last commit before squash merge, so the ledger lands on `main` naturally
without any bypass. Loop-break: `sandbox-e2e` and `claude-review` detect the `chore(cost):` prefix
and skip their expensive steps; all required checks still report success. `pr-cost-finalize.yml`
retains only the post-merge analysis comment; the broken commit-to-main step is removed.

## 2026-06-04 — BHAGA status doctor CLI (feat/bhaga-status-doctor)

Added `agents/bhaga/scripts/status.py` — a read-only ops freshness checker that answers "did yesterday's run land in Sheets, BigQuery, and Grafana?" with one command so a cold agent on any machine never has to re-derive coordinates or hand-write queries.

- **Deliverable A:** `status.py` — checks all three layers (Sheets `data_window_end`/`daily`/`tip_alloc_daily`, BQ model_*/raw tables, Grafana vw_* views), exits nonzero if any layer is missing the date. Single declarative registry (`BQ_TARGETS`, `GRAFANA_VIEWS`, `KNOWN_UNCHECKED_GRAFANA_REFS`) is the introspection target for anti-drift tests. Supports `--json` for scripting and `--check-schema` for live INFORMATION_SCHEMA validation.
- **Deliverable B:** Discovery wiring — one-liner in `bhaga-principles.md` + catalog row in `scripts/README.md` so a fresh agent finds it without spelunking.
- **Anti-drift:** 3 sync tests in `test_status.py` parse `dashboard.json` + migration SQL to enforce registry coverage; `check_doc_freshness.py` coupling makes a migration/dashboard PR that skips updating `status.py` a **hard CI failure**.
- Docs updated: RUNBOOK.md §14 "Status doctor" section added.

## 2026-06-04 — Branch protection: Claude review + Sandbox e2e now required checks

Added `Claude review`, `Sandbox e2e`, `PR Description`, `Doc Freshness`, and `PR cost gate` as **required status checks** in the "Protect Master" ruleset (id 17062025). Auto-merge now waits for all five to pass before merging — previously the ruleset had no required checks, so auto-merge fired immediately on approval regardless of CI state.

## 2026-06-04 — Grafana deploy: cloud-native token (no laptop dep)

`grafana-dashboard-sync` was failing post-merge of #28: `deploy.py` resolved
`GRAFANA_API_TOKEN` from env but then unconditionally wrote it into macOS
Keychain via `security`, which doesn't exist on the Linux runner. Fix (in #30):
`provision.get_api_token` now resolves the env var first and only falls back to
Keychain locally (returning `None` instead of crashing when `security` is
absent); `store_api_token` no-ops gracefully off-macOS; `deploy.py` drops the
pointless CI-path Keychain write. Bootstrapped `GRAFANA_API_TOKEN` +
`GRAFANA_ORG_SLUG` into GitHub repo secrets. Verified: a `workflow_dispatch`
run deployed the dashboard green using the env token (RUNBOOK §0 — no
laptop/Keychain dependency).

## 2026-06-04 — babysit + post-merge CI + multi-requirement consolidation

Four improvements consolidated into one PR (`feat/babysit-postmerge-ci-consolidation`):

1. **babysit skill** (`~/.cursor/skills-cursor/babysit/SKILL.md`): explicit loop with `state==MERGED` check at top — exits immediately on merge; adds Post-merge CI section to watch `post-merge-ci.yml` after merge.
2. **post-merge CI** (`.github/workflows/post-merge-ci.yml`): new workflow triggered on `pull_request: closed` + `merged == true`; runs sandbox e2e and a Claude post-merge audit on the merged code and posts evidence + cost stats to the merged PR.
3. **multi-requirement consolidation** (`scripts/new_requirement.py`): `--requirement` is now repeatable; multiple requirements go into one worktree/PR by default. Pass `--split` to create one PR each.
4. **handoff always opens Cursor** (`scripts/new_requirement.py`): removed `--no-open-cursor` flag — Cursor is always opened; launcher HTML is the fallback only when the Cursor CLI is not found.

## Recurring Mistakes (read before every task)

| Mistake | Where the fix lives | Pre-check |
|---------|---------------------|-----------|
| Compared `2025` folder against itself (0 diffs = meaningless) | `orchestrator.py` `validate_against_benchmark()` | Verify shadow_folder_id != benchmark_folder_id |
| Copied folder structure from sealed `2025` benchmark | `derive_registry_from_return.py` | Never read `Taxes/2025` to decide what to create in `2025-test` |
| User correction acknowledged in conversation but not persisted | `.cursor/rules/jarvis.md` Hard Lessons + skill-evolution protocol | Every correction = a file write. Name the file or it didn't happen. |
| Asked user what could be self-checked (county, portal availability) | `chitra-playbook.md` Step 4 triage table | Derive from address/portal before asking |
| Validation done once at end instead of after each action | `orchestrator.py` `upload_and_validate()` | After each upload/folder creation, re-inventory and diff |

## 2026-06-03 — Dedicated bot account for all agent GitHub operations

**Decision:** All Jarvis agent GitHub operations now run as `jarvis-agent-bot328` (not `aditya2kx`).

- `jarvis-agent-bot328` is a Write collaborator on `aditya2kx/jarvis`; its classic PAT (`repo` + `workflow` scopes, no expiry) is stored in Keychain under `github-bot-pat`.
- `GH_TOKEN` in `~/.zshrc` always resolves to the bot PAT — every `gh` / `git push` from an agent session appears on GitHub as the bot.
- **Server-side merge lock:** `main` branch protection requires 1 approval + `require_last_push_approval: true`. Since the bot is always the last pusher it cannot approve its own PRs; only `aditya2kx` can approve → merge unlocks.
- **Aliases for operator personal use:** `gh-adi` / `git-adi` (personal account), `gh-jarvis` / `git-jarvis` (bot, explicit).
- Updated: `~/.zshrc`, `~/.gitconfig` (bot local config for jarvis repo), `CONTRIBUTING.md`, `RUNBOOK.md`, `jarvis.md` (Conventions + Hard Lesson #20).

## BHAGA Agent (Tip Allocation & Payroll Prep)

### 2026-06-03 — PR #23: BHAGA P0 operational fixes (BQ IAM + Square trusted-device)

Three operational issues, all diagnosed against live prod (Cloud Run logs + IAM + BQ):

1. **BQ incremental run failed (root cause).** The orchestrator SA
   `bhaga-orchestrator@jarvis-bhaga-prod` held **no BigQuery roles**, so every BQ job returned
   `403 …bigquery.jobs.create`. The non-fatal `load_bigquery` / `materialize_model_bq` steps swallowed
   it, so the nightly stayed green while the BQ mirror silently stalled. `core.datastore.read_query`
   also swallowed the 403 into `[]` → `materialize_model_bq` crashed with a misleading
   `max() iterable argument is empty`. **Fixes:** granted `roles/bigquery.jobUser` +
   `roles/bigquery.dataEditor` to the SA; `read_query` now **re-raises** access errors;
   `materialize_model_bq` raises a precise breadcrumb on empty raw. Verified by re-running the BQ steps
   **as the SA** (Cloud Run job) — they now succeed.
2. **Square prod always prompted magic link / 2FA.** The prod job lacked `BHAGA_SESSION_PERSIST=1`
   (only `sandbox_live_run.py` set it), so `persist_session`/`restore_session_path` no-op'd → no
   trusted-device `storage_state` was ever saved/restored. **Fix:** set `BHAGA_SESSION_PERSIST=1` on the
   prod job and codified it in `deploy.yml` (survives a job recreate). First post-fix nightly still does
   one login to seed the session; subsequent runs reuse it.
3. **BQ trailed Sheets by a day** (same 403 root cause). **Fix:** re-backfilled via the RUNBOOK §14
   command-override path (no OTP); BQ raw + model are now current at `2026-06-03` (was `2026-06-02`).

Code: `core/datastore.py` (re-raise BQ access errors), `agents/bhaga/scripts/materialize_model_bq.py`
(empty-raw guard), `.github/workflows/deploy.yml` (`BHAGA_SESSION_PERSIST=1`), new tests
(`core/test_datastore_access_error.py`, `agents/bhaga/scripts/test_materialize_empty_guard.py`). Docs:
RUNBOOK §3 env table, §14 SA-IAM + incident note.

### 2026-06-02 — Revive ADP-paid reconciliation + guard against migration regressions

**Regression (root cause).** Commit `6f87f9c` ("remove earnings XLSX dependency from model rebuild")
stubbed `actual_cc_tips_by_period(None)` in `update_model_sheet.py`, so `adp_paid`/`diff`/`diff_pct`
went permanently `N/A` for every closed period, and `period_summary.check_dates` went permanently
empty. The commit framed it as intentional ("gracefully show N/A without earnings data"), and the one
prod-like CI gate **encoded `N/A` as the EXPECTED value** — so nothing flagged it. A human had to
eyeball the sheet. This was a silent **semantic** regression from the laptop→cloud migration: every
mechanical guard (row counts, `data_window_end` advanced, KDS join) still passed.

**Fix (M1) — re-wire, not rebuild (derive from existing cloud data, no new tab).** The Earnings XLSX
(source of "Credit Card Tips Owed") is still cached in GCS at `gs://bhaga-scrape-cache/<date>/adp/
Earnings-*.xlsx`. `update_model_sheet.load_cc_tips_earnings_from_gcs` enumerates cached dates in the
window, downloads **only** the Earnings artifact (`gcs_cache.download_cached_files(name_contains=…)`),
parses via `compensation_backend.parse_xlsx`, unions across dates (deduped), and feeds
`actual_cc_tips_by_period(earnings)`. `check_dates_by_period` revives the check-dates column. Closed
periods with no covering export in GCS (older than the cache's ~2026-05-29 inception) show a **distinct**
reason ("No ADP earnings export in GCS for this period"), not the old blanket `N/A`. **adp_paid feeds
ONLY the verification columns, never `our_calc`/allocations** → worst case is a blank comparison, not
corrupted pay → shipped on by default, no flag.

**Prevent (M2) — standing semantic post-conditions.** New `model_semantics.py` is the single source of
truth (pure functions) shared by `sandbox_e2e` (per-PR) and `daily_refresh` (nightly): tip-pool
conservation, closed-period `adp_paid` reconciliation, and review-bonus survival. A semantic failure
clears the `update_model_sheet` marker (rerun rebuilds) + alerts. The CI fixtures that blessed the bug
are gone.

**Reconciliation is CADENCE-SAFE (corrected after the first CI run).** The first sandbox run on this
branch tripped the new guard: the latest closed period (5/18–5/31) showed `adp_paid=N/A` and a naïve
"latest closed period must reconcile" assertion failed it. Ground-truth from the GCS Earnings exports
proved the `N/A` was **correct**: 5/18–5/31's export (check date 6/01) carries **zero** "Credit Card
Tips Owed" lines — only a misc reimbursement — because that payroll hasn't run yet, while the prior
**paid** period 5/04–5/17 has 18 CC-tip lines totalling **$2,358.94** across 9 employees that the loader
keys exactly to the model period. So both guards now gate on
`update_model_sheet.period_has_cc_tip_actuals` (a covering export must actually contain CC-tip lines for
that exact period) and assert via `model_semantics.assert_period_reconciled`; a just-closed/unpaid period
is SKIPPED, not failed. The too-strict `assert_adp_reconciliation_present` was removed (no safe caller —
deciding "this period should be paid" requires the cadence probe regardless).

**Prevent (M3) — auto-halt + resume circuit breaker.** A semantic failure trips a GLOBAL halt flag
(`state_adapter.{get,set,clear}_pipeline_halt`; Firestore `<collection>/_pipeline_state` / local file).
While tripped, fresh runs refuse and exit `EXIT_HALTED` (=3, distinct from the OTP-pending `return 0`);
`--ignore-halt`/OTP-resume pass through; a healthy verified run auto-clears it. In-job Firestore gate
(no Cloud Scheduler API / new IAM).

**Audit (M4) — pre/post-migration column review.** The empirical column-by-column diff on a known-good
closed period runs as the per-PR `sandbox_e2e --source prod-raw --period last-closed` evidence. Latent
findings triaged:
- `adp_paid`/`diff`/`diff_pct` (dead) — **FIXED (M1)**; now guarded (M2).
- `period_summary.check_dates` (empty) — **FIXED (M1)**.
- Mon/Tue earnings **cadence**: a period's check date is issued after its end, so the export lands days
  later — the loader unions across cached dates + a 21-day look-ahead, so cadence no longer causes a
  miss once any covering export exists. Pre-cache periods (before ~2026-05-29) legitimately stay `N/A`.
- **Follow-ups (tracked, not fixed here):** (a) `wage_rates` can go stale on an OTP-skip / empty-cache
  path (`backfill_from_downloads`) — needs its own staleness guard; (b) item/KDS WARN-skips are still
  soft — candidate for a future semantic check once coverage windows are formalized; (c) the nightly
  cadence probe (`period_has_cc_tip_actuals`) re-lists+re-downloads the Earnings XLSX that
  `update_model_sheet.main()` already loaded for the build (bounded, ~5–10 files/run, different windows
  so a naïve memo won't dedupe) — thread the loaded earnings out of `main()` to drop the second fetch.

**Why a human had to prompt this (process fix).** CI asserted the dead state was correct and no
semantic post-condition existed, so the agent had no signal. The standing semantic guard (M2) + breaker
(M3) convert "a human eyeballed the sheet" into a loud, automatic check; per-PR verification stays
change-local (CONTRIBUTING §6) while the nightly guard watches the rest.

### 2026-06-02 — Sandbox now PROVES the exemption (overlay mirror + tip_alloc_period verify, PR #10)

- The mandatory prod-data `Sandbox e2e` previously proved **conservation** but never the
  **exemption** (it seeded prod raw Square+ADP but not the human-owned `training_shifts` overlay,
  so the sandbox built with an empty overlay) and the PR carried no per-scenario evidence report.
  Both gaps closed.
- **Prod overlay populated:** wrote the real 5/18–5/31 exemptions to the human-owned **prod**
  `training_shifts` tab — `Padron, Lisette 2026-05-23` + `Urrutia, Emely 2026-05-23` (Lisette's only
  worked day in the period; Emely's one training day), preserving the existing operator rows
  (Juan 5/18, Ximena 5/29 + 5/31).
- **Sandbox mirrors it:** `seed_sandbox_training_shifts_from_prod` copies the windowed prod overlay
  into the sandbox model (read-prod/write-sandbox, same hard isolation as the raw seed), so the
  sandbox build applies the SAME exemptions as prod.
- **New verifier `assert_exemptions_applied`** (data-driven, no hardcoded names): proves each worked
  training shift is dropped from `tip_alloc_daily`, the day's pool redistributes to the rest,
  whole-period-exempt staff earn $0 over the period while partially-exempt staff keep their
  non-exempt earnings (exempt-day hours removed from the denominator), and the period conserves.
- **Verified on real 5/18–5/31 prod data (sandbox):** 5/5 worked exempt shifts dropped; whole-period
  → **Lisette + Ximena $0** (absent from `tip_alloc_period`); partial → **Juan $95.10**,
  **Emely $82.43** (hours 19.2, the 8.22h 5/23 shift removed); on 5/23 the full **$169.66** pool
  goes to the 4 non-exempt staff; period our_calc **$1,197.77 == pool**, conservation 0¢ over 12 days.

### 2026-06-02 — Per-shift training tip-exemption overlay (PR #10)

- **New `training_shifts` overlay tab** (human-owned, `employee_name | date | note`): marks a specific
  `(employee, date)` as **tips-exempt** — the day's hours are dropped from the **tip** denominator only
  (labor% unaffected), so the full pool redistributes to the other tipped staff. Reader
  `_read_training_shifts_from_sheet` mirrors `_read_training_excluded_from_sheet`; threaded through
  `_is_excluded`, `build_daily_rows`, `build_period_results`, `main()`, and `verify_bq_parity` so parity
  stays honest. The existing `training_excluded:<name>` through-date shorthand is **kept** (bulk
  shorthand + precise per-shift marks coexist).
- **`write_training_shifts`** added to `tip_ledger_writer` (create-if-missing + idempotent
  `(employee,date)` upsert; preserves operator-added rows) so the tab is maintained through the writer
  skill, not ad-hoc Sheets calls. Tab registered in `palmetto.json`.
- **Verification (two layers).** (1) *Isolated sandbox (CI gate):* the per-PR `Sandbox e2e` rebuilds
  the model against isolated sandbox sheets, which exercises `_read_training_shifts_from_sheet` on
  every build (reader runs unconditionally; absent/empty tab → empty overlay, covering the read +
  parse + graceful-missing path this PR adds to the build). (2) *Manual prod rebuild (supplementary):*
  local rebuild via impersonated `bhaga-orchestrator` SA — Lisette `training_excluded:2026-05-31` +
  Ximena (5/29, 5/31) + Juan (5/18) → Lisette/Ximena $0, Juan 5/18 dropped, **pool conserved at
  $1,197.77**. The populated-overlay → exclusion → **cent-exact conservation** end-to-end on prod data
  is machine-checked by the mandatory prod-data sandbox gate landed in **this same PR** (see next
  entry). **Durability note:** per-shift marks only stick once this PR is deployed; the nightly cron
  stays paused until then (the deployed image can't yet read the tab).

### 2026-06-02 — Mandatory per-PR prod-data sandbox verification (same PR #10)

- **Two-tier sandbox mandate** (CONTRIBUTING): Tier 1 = the per-PR `Sandbox e2e`, now a no-OTP
  **prod-data** run — reads the PROD raw Square+ADP sheets directly for the most-recent **closed** pay
  period and writes only to a leased sandbox slot (read-prod / write-sandbox, hard-asserted), rebuilds
  the model, and verifies the full period incl. **tip-pool conservation**. Because it never scrapes or
  logs in, it blocks merge on every PR (no opt-out). Tier 2 = the live-OTP `sandbox_live_run` scenario,
  kept on-demand for live-only paths (selector/login/2FA).
- **New code:** `most_recent_closed_period` (pure, reuses the `discover_periods` anchor math) in
  `update_model_sheet.py`; `seed_sandbox_raw_from_prod` + `filter_rows_to_window` +
  `assert_tip_pool_conserved` in `sandbox_e2e.py`; `--source {gcs-replay,prod-raw}` + `--period
  last-closed` CLI. The no-OTP structural guarantee (`test_sandbox_e2e_no_otp`) still holds — only
  reader/writer/model modules enter the import graph.
- **Read-prod / write-sandbox guard:** the staging isolation guard now distinguishes read vs write
  (`_assert_not_production_sheet(..., op=)`). Prod *writes* stay hard-blocked; prod *reads* are only
  unlocked inside an explicit `allow_production_read()` scope (used solely by the seed step), so the
  seed can copy real prod raw rows while a misrouted write still fails closed.
- **Wiring:** `.github/workflows/sandbox-e2e.yml` runs `--source prod-raw --period last-closed`; stays
  the required per-PR status check (**fail-fast** if `SANDBOX_E2E_ENABLED` is unset — red, never a
  silent skip).

### 2026-06-01 — Cloud observability, sandbox isolation, JSON selectors, live sandbox run

- **Incident (2026-05-31 item sales):** both nightly attempts raised `RuntimeError: Item Sales page
  date picker not found within timeout` — Square UI **selector drift**. Root cause was readable from
  Cloud Run **logs** (no rerun), but the screenshot/DOM were written to the container's ephemeral
  `~/.bhaga` and **lost** — the observability gap this change closes.
- **M1 — observability:** `_capture_failure_evidence` now uploads screenshot + DOM + meta to
  `gs://<cache>/<date>/evidence/` (lazy import, best-effort, greppable `gs://` breadcrumb). The URI is
  threaded into the Slack failure DM (`notify.failure_alert(evidence_uri=…)`) and the Firestore
  `runs/<date>` doc per failed step (`state_adapter.record_step_failure`). Complete per-run visibility
  for postmortems without a rerun.
- **M2 — sandbox isolation (read prod, NEVER write prod):** hard guards on the three write paths —
  sheets (`config_loader._assert_not_production_sheet`), GCS cache
  (`gcs_cache._assert_sandbox_write_isolation` + `BHAGA_GCS_CACHE_WRITE_BUCKET`), and run-state
  (`state_adapter._assert_sandbox_state_isolation` + `BHAGA_FIRESTORE_COLLECTION`). Added to
  `bhaga-principles.md` / `bhaga.md`.
- **M3 — selector robustness:** item-sales date-picker + export selectors externalized to
  `square_tips/selectors/item_sales.json` with resilient fallbacks; `runner._find_item_sales_pill`
  tries JSON-driven patterns/locators in order. The exact fix for drift is now a **one-file** edit.
- **M4 — incremental cache:** each Square artifact is uploaded to GCS immediately after download, so a
  later-step failure (like item sales) never discards already-scraped transactions.
- **M5 — live sandbox run + scenario suite:** `sandbox_live_run.py` deploys unmerged PR code to
  `bhaga-sandbox-refresh` (self-wires by **inheriting prod's secrets + SA** — same creds, only the
  isolation env differs) and runs a **real** scrape against a leased sandbox slot. `sandbox_scenarios.py`
  organizes runs as a named suite (`item-sales-live`, `full-live`) selectable three ways via
  `.github/workflows/sandbox-live-run.yml`: committed `.github/sandbox-live.yml` + `sandbox-live` label
  (`pull_request`, works **pre-merge**), `/sandbox run <scenario> [date=…]` PR comment (`issue_comment`,
  post-merge), or manual dispatch. Forks refused; comment commands require OWNER/COLLABORATOR/MEMBER;
  evidence auto-posted as a PR comment. Isolation pre-flight fails before any deploy. **OTP routing:**
  prod Slack bot, but the prompt is labeled `[SANDBOX · PR…]` and the pending-OTP checkpoint carries
  routing metadata so the webhook (sandbox collection scanned **first**, default `sandbox_runs`) resumes
  the **sandbox** job, never prod, even under a concurrent prod OTP. Supervised live runs set
  `BHAGA_OTP_ASSUME_READY=1` to wait for the code **inline** (serviced by the existing webhook via the
  agent-keyed `otps` collection), so the OTP round-trip works **even before** this PR's webhook deploys.
- **First live dispatch (2026-06-01, PR #9 `sandbox-live` label):** resolve → build/push image → lease
  sandbox slot → seed model from prod (read-only) → isolation pre-flight all ✅; stopped at the expected
  least-privilege gate (`storage.buckets.create` denied). Bucket creation is now a documented one-time
  operator step (RUNBOOK §13); `assert_sandbox_bucket` fails with the exact remediation instead of
  attempting create.
- **Tests:** +new unit suites (`test_gcs_cache`, `test_runner_item_sales`, `test_sandbox_live_run`,
  `test_notify`) and extended `test_state_adapter` / `test_handler` (sandbox routing). 399 BHAGA tests green.
- **Status: in PR `feat/bhaga-cloud-observability`.** Live reproduction of 5/31 + the exact selector
  calibration are **operator-gated** (trigger the workflow + supply OTP); prod 5/31 + 6/1 reruns are
  post-merge, after suspending `bhaga-nightly`.

### 2026-06-02 — Live-run hardening: prod-job inheritance, magic-link relay, trusted device, scoped scenario

- **Operator setup done (no longer a blocker):** created `gs://bhaga-scrape-cache-sandbox` + granted the
  run SA (`bhaga-orchestrator@…`) bucket-scoped `storage.admin`. First real sandbox execution then ran.
- **Sandbox-job config inheritance (two real bugs the live run surfaced):** `gcloud run jobs describe
  --format=json` emits the **KRM/v1** shape (deep nesting, `valueFrom.secretKeyRef` name/key), not the v2
  shape the parsers assumed — so secret/SA inheritance silently produced an unconfigured job. Parsers are
  now schema-robust (recursive search) and also inherit **cpu/memory/timeout/maxRetries** (a default job
  is 512Mi/600s → OOM/timeout a Chromium scrape) and **prod's plain env vars** (`BHAGA_SECRETS_BACKEND=gcp`
  etc. — without it the loader fell back to a missing `config.yaml` → FileNotFoundError). Isolation overlay
  still layered on top and always wins.
- **2026-06-01 incident — Square escalated an unrecognized device to an email magic link** ("Magic link
  sent. Use this device to sign in.") instead of the SMS code; the code-entry flow can't satisfy it.
  Captured to GCS evidence (observability win — diagnosed with zero reruns). Two-layer fix:
  - **1st line — trusted device:** tick "trust this device for 30 days" during 2FA + persist the Square
    `storage_state` (cookies) to GCS (`<bucket>/_session/square-<store>.json`) and restore it next run, so
    Square recognizes the device and stops escalating. Opt-in `BHAGA_SESSION_PERSIST=1`; sandbox keeps its
    OWN session in the sandbox bucket (isolation preserved). Augments — does **not** restore — the
    2026-05-17 ephemeral default (persists only the cookie JSON, not a user-data-dir).
  - **fallback — magic-link relay:** `runner._is_magic_link_sent` detects the page; `_handle_magic_link`
    DMs the operator to **paste the magic-link URL** (explicitly: do NOT click on phone — the link only
    works in the requesting browser) and `page.goto`s it in the container. New `adapter.request_reply`
    handles the free-form URL reply (unwraps Slack `<url|label>`).
- **Scenario scoped to the failure:** `item-sales-live` now skips ADP/reviews/model (Square-only download)
  via a scenario `skip` list → `sandbox_live_run --skip` → `BHAGA_SKIP_<STEP>` env (read by
  `daily_refresh.main`, ORed with CLI flags).
- **Verification gate:** `sandbox_live_run.verify_item_sales` asserts `<date>/square/items-*.csv` exists
  with >0 data rows; a "green" `item-sales-live` run now truly means item-sales downloaded (catches
  "job exited 0 but the deliverable wasn't available"). Surfaced in the PR evidence comment.
- **Step-by-step screenshot trace (see the whole flow, not just the failure):** new
  `runtime.trace_step(page, label)` captures the FULL browser after each login + item-sales action and
  uploads it to `gs://<bucket>/<date>/trace/NN-<label>.png` (e.g. `landing`, `email-filled`,
  `otp-code-screen`, `magic-link-sent-page`, `magic-link-navigated`, `magic-link-result`,
  `item-sales-page`, `item-sales-exported`). Best-effort/never-raises; off by default, enabled by
  `BHAGA_TRACE_SCREENSHOTS=1` (set automatically for sandbox runs in `build_sandbox_env`, off for the prod
  nightly). Honors sandbox isolation via `gcs_cache` write bucket. This is what answers "show me a
  screenshot of every step" with zero reruns.
- **Magic-link relay ROOT CAUSE (2026-06-02 live run, found via the new trace):** the trace frames
  (`magic-link-sent-page → magic-link-navigated → magic-link-result`) showed we navigated to the pasted
  link but bounced back to "Magic link sent" with a **blank email**. Cause: **Slack HTML-escapes `&`→`&amp;`
  in message `text`**, so a magic link `…?rml=1&token=ABC&uid=123` arrived as `…?rml=1&amp;token=ABC&amp;uid=123`;
  the old unwrap only stripped the `<…>` Slack link wrapper and left the `&amp;`, corrupting the query
  string (`amp;token=…`) so Square rejected the token. Fix: `adapter._clean_slack_reply` now unwraps the
  link **and** `html.unescape`s the text (literal `&`); `_handle_magic_link` extracts the URL with a regex
  (tolerates surrounding text), accepts the `app.` subdomain, and logs `_redact_url_values(url)` (keys kept,
  values redacted) so we can prove the URL is well-formed without leaking the one-time token.
- **SELECTOR DRIFT ROOT-CAUSED + FIXED (the original 2026-05-31 incident, reproduced live):** with login
  finally solved, the sandbox run reached the item-sales page and **reproduced** the "date picker not found"
  failure (trace `item-sales-pill-not-found` + verify gate red). The captured DOM
  (`…/evidence/square-fail-20260602-053441.html`) shows Square **unified item-sales onto the shared
  date-filter dropdown** (same as KDS/transactions): the control is now a single-date dropdown trigger
  `[data-test-sq-date-filter-dropdown-trigger]` (button text = current date, e.g. `05/31/2026`) with prev/next
  arrows, NOT the old `MM/DD/YYYY` range pill; the popover exposes `.begin-date input.input-date` /
  `.end-date input.input-date`. Fix is **JSON-first** as designed: `selectors/item_sales.json` gains
  `primary_locators` (the data-test hook, tried FIRST with a 45s wait since the Ember filter bar renders
  slowly post-`domcontentloaded`) and `range_input_selectors`; `runner._find_item_sales_pill` tries the hook
  first and `_set_item_sales_date_range` fills the explicit begin/end inputs (KDS-style). `last_verified`
  bumped to 2026-06-02.
- **Trace timing fix:** `item-sales-page` is now captured *after* the pill finder returns (page settled),
  not immediately post-`goto` (which produced blank SPA frames).
- **Tests:** +`test_runner_item_sales` (primary data-hook tried first; range inputs present),
  +`test_runner_magic_link` (URL-from-surrounding-text, `app.` subdomain, redaction),
  +`test_adapter_request_reply` (`&amp;` decode regression), extended `test_sandbox_live_run`
  (schema shapes, plain-env inheritance, skip-steps, item-sales verify) + `test_sandbox_scenarios`
  (scoping) + `test_runtime` (trace_step: disabled no-op, full-page upload w/ seq+slug label, never-raises).
  480 BHAGA tests green.
- **Review round (PR #9 Claude bot, addressed inline):** dropped the dead `total_timeout_ms` param on
  `_find_item_sales_pill`; thread the found `pill` into `_set_item_sales_date_range` (no double pattern
  sweep / TOCTOU); `cloud/webhook/handler.py` `SANDBOX_RUNS_COLLECTION` now **defaults to `""`** (sandbox
  OTP scan OFF → prod READY path byte-for-byte unchanged, matching the PR §4 / RUNBOOK claim; set
  `=sandbox_runs` to opt in); `sandbox_workflow_resolve._yesterday_ct` UTC fallback anchored to **UTC-6
  (CST)** so it can't compute "yesterday" a day early; the committed `.github/sandbox-live.yml` + label were
  already removed. **Design fix so this isn't skipped again:** `scripts/check_pr_review_replies.py` is a new
  merge-readiness gate (like `check_doc_freshness`) that fails if any inline review thread lacks a reply;
  wired into CONTRIBUTING's merge-ready definition + the reply-inline policy.
- **✅ VALIDATED GREEN end-to-end (live sandbox, run `26800841808`, commit `747beaa`):** `rc=0`,
  `verify(item_sales): item-sales OK — …/items-2026-05-31-2026-06-01.csv (502 data rows)`. The trusted-device
  session persisted from the prior magic-link login was restored, so **Square skipped 2FA entirely** (no OTP /
  no magic link — `already-logged-in-dashboard` trace), then the new date-dropdown selector found the control,
  set START/END `05/31/2026`, and exported the Detail CSV. Closes the 2026-05-31 incident on live data; the
  committed `.github/sandbox-live.yml` + `sandbox-live` label were removed afterward so future pushes don't
  auto-fire a live scrape (re-run on demand via `/sandbox run item-sales-live`).

### 2026-06-01 — Browser-launch resilience, OTP-portal recovery, principles consult-first

- **Incident (2026-05-31 nightly):** Square's Chromium died on launch (`TargetClosedError` in
  `skills/_browser_runtime/runtime.py`) — a transient container crash (ADP launched fine ~1s later).
  Square failed after ADP succeeded, so the downstream steps ran on stale 5/30 data and were marked
  done; `data_window_end` stuck at 5/30 and 24 review bonuses held back.
- **M1 — browser resilience:** `launch_persistent` now retries the launch _setup_ (not the yielded
  body, never an auth/2FA error) on transient crashes with a full driver restart + exponential backoff;
  headless-only container-stability flags (`--disable-dev-shm-usage`/`--no-sandbox`/`--disable-gpu`);
  greppable breadcrumbs; new `browser_healthcheck()` pre-flight smoke test. Config:
  `BHAGA_BROWSER_LAUNCH_RETRIES` / `BHAGA_BROWSER_LAUNCH_BACKOFF_MS`. `test_runtime.py` (13 tests).
- **M2 — recovery:** `state_adapter.clear_step` (local + Firestore `DELETE_FIELD`) +
  `daily_refresh._recover_stale_downstream_markers` invalidate stale downstream markers when an OTP
  portal recovers. **Always on (no feature flag)** — safe by construction (idempotent upserts +
  post-condition guard verifies `data_window_end` advanced). Per the refined CONTRIBUTING flag policy:
  only flag when a change could corrupt the numbers; this can't.
- **M3 — principles consult-first:** new always-on `.cursor/rules/bhaga-principles.md`; `AGENTS.md`
  consult-before-design directive; `jarvis.md` frontmatter + breadcrumb / no-reflexive-retry
  conventions; HL#8 cloud nuances promoted into `bhaga.md` (so cloud agents see them).
- **M4 — docs/freshness:** RUNBOOK §13 browser-resilience + recovery + the exact **5/31 recovery
  runbook** (post-merge, operator-announced OTP); README code map; new `check_doc_freshness` couplings
  for `_browser_runtime` + `state_adapter`.
- **Status: in PR `feat/browser-resilience-and-recovery`.** 5/31 prod rerun is post-deploy.
- **Follow-up (tracked here):** M1 has no real-Chromium-crash e2e (the sandbox replay has no headless
  browser) — if a container e2e harness is added later, cover the `TargetClosedError` retry path there.

### 2026-05-30 — Item-level operations tab (`item_lines` + `item_operations`)

- **Raw `item_lines`:** persists every Square Item Sales Detail line (natural key includes
  `line_seq`). Nightly `backfill_from_downloads` upserts gap rows; historical replay via
  `backfill_item_lines_from_cache` (GCS `items-*.csv` by default, no extra OTP).
- **Model `item_operations`:** item sale time + `staff_punched_in_{hourly,fulltime,total}_count`
  from ADP punches at `item_sold_at_local` (`skills/bhaga_labor/staff_punched_in.py`). Incremental
  upsert on each `update_model_sheet` run for the gap window.
- **Docs:** `agents/bhaga/knowledge-base/DOMAIN.md` §3B/§3D; RUNBOOK backfill commands.
- **Tests:** `test_item_lines.py`, `test_staff_punched_in.py` (golden day S1), reconciliation S2,
  pipeline e2e.

**Status: SHIPPED & CLOUD-PRIMARY (2026-05-29). Nightly runs as a GCP Cloud Run Job; laptop retired.**

> **Operate from [`RUNBOOK.md`](RUNBOOK.md).** Behavioral spec: [`.cursor/rules/bhaga.md`](.cursor/rules/bhaga.md).
> Code map + how to extend the model: [`agents/bhaga/scripts/README.md`](agents/bhaga/scripts/README.md).
> Entry point for any machine/cloud agent: [`AGENTS.md`](AGENTS.md). The M1–M4 milestones and
> open-questions below are **historical** — all resolved; kept for provenance.

**Current state (2026-05-29):**
- **Pipeline live in cloud.** `bhaga-nightly` Scheduler (21:30 CT) → `bhaga-daily-refresh` Cloud Run
  Job (`daily_refresh.py`): scrape Square/ADP → mirror to raw sheets → recompute Model tabs →
  reviews → Slack heartbeat. OTP/2FA via Firestore + `bhaga-webhook` (no laptop listener).
- **Model tabs:** `config, daily, labor_daily, labor_weekly, labor_period, tip_alloc_period,
  tip_alloc_daily, period_summary, review_bonus_period, labor_daily_forecast`. All derived from the
  raw sheets (`bhaga_adp_raw`, `bhaga_square_raw`, `bhaga_review_raw`).
- **Sheet source of truth:** `store-profiles/palmetto.json` `google_sheets` block (staging mode +
  `google_sheets_staging` retired in the 2026-05-29 cutover).
- **Timezone:** all date selection + reports in Central (`America/Chicago`).
- **Recent fixes:** `review_bonus_period` now rebuilds unconditionally (commit `4059604`); sheet
  config consolidated to a single source of truth; staging-isolation tests made synthetic.
- **What's next / backlog:** extend the model as needs arise (see scripts/README § Extending the
  model); finish laptop-decommission checklist (`RUNBOOK.md` §11) — keep credentials in an
  independent password manager off the Keychain.

**Docs system + lock-step enforcement (2026-05-30):** made the repo a self-sufficient, cross-device
source of truth. Added `AGENTS.md` (canonical entry point + doc map + work-from-any-machine guide),
rewrote `.cursor/rules/bhaga.md` and `agents/bhaga/scripts/README.md` to cloud reality (incl.
"Extending the model" recipes), added RUNBOOK §12 Operating rules + §13 Common tasks. Enforcement
(so it's not just prose): `.cursor/rules/doc-maintenance.md` (auto-loads on code edits, maps
code→doc), `scripts/check_doc_freshness.py` (deterministic checker, `--strict` for CI,
self-maintaining `COUPLINGS` table), and `.github/workflows/doc-freshness.yml` (non-blocking CI
signal on push + PRs). Git-hook approach rejected: local hooks don't travel, portable hooks need a
forbidden git-config change that would shadow the corporate pre-push hook.

**Per-PR sandbox e2e — prod-like, zero-OTP (2026-05-30):** added `agents/bhaga/scripts/sandbox_provision.py`
(creates/tears down 4 ephemeral sandbox sheets per PR, seeds model `config`+`employees` read-only from
prod, emits `BHAGA_STAGING_*_SID`) and `agents/bhaga/scripts/sandbox_e2e.py` (provision → GCS-cache
replay → backfill → model build → `assert_model_tabs_populated` → evidence → teardown). It runs on
every PR via `.github/workflows/sandbox-e2e.yml` (+ `sandbox-teardown.yml` on close), reusing
deploy's WIF, gated behind the `SANDBOX_E2E_ENABLED` repo var. **Structural no-OTP guarantee:** the
runner composes only replay code and imports no Square/ADP/ClickUp/browser module — to make that hold,
`daily_refresh.py`'s scrape imports were made lazy (importing it, or `update_model_sheet`, no longer
pulls in `patchright`/runners). `test_sandbox_e2e.py` enforces the guarantee in an isolated
interpreter. Reviews stay out of scope (live ClickUp); item-ops auto-included once it lands on main.
**Sandbox pool + CI fix (2026-05-30):** Replaced per-PR sheet *creation* (SA can't create on consumer
Drive) with a 3-slot pre-shared pool (`sandbox_pool.json`, operator `create-pool` as palmetto user).
CI leases via Firestore `sandbox_slots`, clears/writes, releases. Enabled Drive API on
`jarvis-bhaga-prod`; local full e2e green with ADC (`aditya.2ky@gmail.com`) + palmetto OAuth.
**Claude review cost cap (2026-05-30):** Switched PR bot from Opus/40 turns (~$4–5/PR, ~4.7M input
tokens) to Sonnet 4.6/10 turns + diff-only prompt (~$0.50–1 target). Added
`scripts/post_claude_review_cost.py` — posts a PR comment after each review with model, turns,
tokens, and reported USD from `execution_file`.
**Claude review bounded context (2026-05-30):** `scripts/build_claude_review_context.py` materializes
PR-changed files + paired tests + rubric into `review-context/` so the bot can Read cross-file
context without repo-wide grep (see CONTRIBUTING.md § Review bot).

Follow-up (2026-05-30): addressed Claude review's non-blocking notes on PR #3 — clarified `select_window`
returns the span across the N most-recent *cached* dates (not N calendar days), flagged the bounded
`seed_model_metadata` read ranges as a truncation risk, and noted in RUNBOOK §13 that the first PR landing
after `SANDBOX_E2E_ENABLED=true` is the live-validation of the harness.

**Dev-process gaps closed (2026-05-30):** (1) "cloud reads from the cloud, never laptop files" is now
a hard rule in `AGENTS.md` (rule 6) + `.cursor/rules/bhaga.md` § Operational rules, and enforced in
code via `backfill_item_lines_from_cache.py` defaulting to GCS-only (`--local-only` for tests).
(2) Deploy/run gap: RUNBOOK §13 now has "Run a one-off backfill / maintenance
script against prod" — Option A (Cloud Run job command override + revert) and Option B (ADC shell with
`BHAGA_SECRETS_BACKEND=gcp`), plus a verify step. (3) Autonomy norm: "build & verify are part of the
task — don't ask permission" added to `AGENTS.md` (rule 7) + bhaga.md. (4) Added missing
`skills/bhaga_labor/README.md`, Recipe D (incremental high-volume model tab) to scripts README, and
freshness couplings for `skills/bhaga_labor/**` + `skills/square_tips/transactions_backend.py`.

**PR process + Claude Opus review bot (2026-05-30):** moved off "push to `main` directly." New flow is
branch → PR → automated Claude Opus review + CI → merge → deploy, so features built in other (cheaper-
model) chat spaces stay reviewable. Added: `CONTRIBUTING.md` (the process), `.github/pull_request_template.md`
(required sections: what / motivation / e2e-test-with-evidence / backward-compat + proof / checklist),
`.github/claude-review-guidelines.md` (the rubric the bot enforces — desc completeness, backward compat,
BHAGA invariants, testing, security, docs lock-step), and `.github/workflows/claude-review.yml`
(`anthropics/claude-code-action@v1`, `--model opus`, cost-bounded, **dormant until repo secret
`ANTHROPIC_API_KEY` is set**). Updated AGENTS.md rule 1 / RUNBOOK §12 / bhaga.md to the PR flow; added a
freshness coupling for the process files. **Manual one-time (repo admin):** add `ANTHROPIC_API_KEY`
secret + enable branch protection on `main` (see CONTRIBUTING.md § Enabling enforcement).

Named after **Bhaga** (भग) — Vedic Aditya whose name derives from Sanskrit *bhaj* ("to apportion, divide, share"). The deity of just distribution of wealth and shares — the rightful portion due to each. Etymologically perfect for a tip-pool fair-share agent.

**Origin**: handoff doc at `get open/handoff-tip-allocator-agent.md` (chat: [Square ADP tip automation plan](b8a58719-e992-4051-954d-dbd513cf0f93)). Sibling-pattern reference: AKSHAYA (Square + Playwright + Sheets).

**What existed at scaffold time (2026-04-18 — historical; all since shipped):**
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

**BHAGA backlog — incremental milestones (HISTORICAL; M1–M4 all shipped, now cloud-primary):**

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
- 2026-06-03 (PR #16): **BI tool = Grafana** over Superset / Metabase / Looker Studio. Decider: only tool that offers a true shared crosshair line across charts as a first-class feature AND is fully dashboards-as-code (JSON model + REST API + Terraform), so the agent owns the entire lifecycle. BigQuery datasource uses the existing `jarvis-bhaga-prod` service account. Looker Studio rejected despite native-BQ/free because it has no shared crosshair and no real creation API (Playwright-only) — fails the two headline asks.
- 2026-06-03 (PR #16): **Grafana hosting = Grafana Cloud free tier** over Cloud Run / Cloud Run+Cloud SQL / GCE. Decider: cost (must be free while still proving the stack out) + zero ops + it persists the occasional manual UI tweak. Accepted trade-offs: external Grafana Labs account + BQ query egress from Grafana's cloud. Revisit (move in-project to Cloud Run+file-provisioning) if/when usage grows or egress/security matters.
- 2026-06-03 (PR #16): **BigQuery becomes the source of truth; Google Sheets is to be retired as the analytical store.** Root cause found: the daily cron writes raw + model only to Sheets; `backfill_bigquery.py` was a one-shot Sheets→BQ load that went stale (~5/26) and was never wired into the cron. Plan: backfill the gap, wire incremental BQ writes (raw + materialized `model_*` tables) into `daily_refresh.py`, flip `BHAGA_DATASTORE=bigquery` so the model also reads from BQ, expose curated `vw_*` views as the BI contract, then drop Sheets as the analytical layer.
- 2026-06-04 (PR feat/grafana-dashboard-refactor): **BQ-canonical compute + 3-section Grafana dashboard.** Key decisions: (1) `materialize_model_bq` is now the canonical model producer (not a Sheets mirror); tip-pool conservation check added post-build. (2) `render_model_sheet_from_bq.py` projector added behind `BHAGA_SHEET_FROM_BQ` flag (default off) — Sheet model tabs rendered from BQ when on. (3) `process_reviews.py` dual-sinks `model_review_bonus_period` to BQ (non-fatal) via shared `load_model_rows()` helper. (4) `reconcile_model.py` compares Sheet tabs against BQ tables cell-by-cell (reusing `verify_bq_parity` helpers); CI workflow + non-fatal nightly step. (5) Migration 004 adds `model_review_bonus_period` table + `vw_model_labor_daily` (extended), `vw_model_labor_weekly` (new), `vw_model_payroll_period` (new — joins tips + review bonus + wage rates). (6) Dashboard rewritten into 3 collapsible row sections: Order Volume (daily/weekly orders+items), Labor Cost (daily/weekly labor%+hours/item), Payroll (full-width table via `vw_model_payroll_period`). (7) `docs/FEATURE_FLAGS.md` tracker added. CONTRIBUTING: additive-prod-data-source exception documented. `RUNBOOK.md`: stale `run_migrations` → `ensure_schema` fixed; BQ-canonical path and flip procedure added.

## Git State
- Branch: `main`
- Remote: configured (private SSH key)
- Public URL: https://github.com/aditya2kx/jarvis
