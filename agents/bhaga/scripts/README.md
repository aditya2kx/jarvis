# BHAGA Scripts

Agent-specific orchestration for the BHAGA nightly pipeline. Reusable logic lives in `skills/`
(`square_tips`, `adp_run_automation`, `tip_pool_allocation`, `tip_ledger_writer`, `slack`,
`bhaga_config`); these scripts are the glue that wires those skills together and run **in the cloud**
as a Cloud Run Job.

> **Operate the live system from [`RUNBOOK.md`](../../../RUNBOOK.md).** Behavioral invariants are in
> [`.cursor/rules/bhaga.md`](../../../.cursor/rules/bhaga.md). What the data *means* (orders, items,
> labor, hourly vs full-time, KDS, tips, reviews, every metric) is in the domain data dictionary
> [`../knowledge-base/DOMAIN.md`](../knowledge-base/DOMAIN.md). This file is the **code map** + the
> guide for **extending the data model**.

---

## The nightly pipeline (what `daily_refresh.py` does)

Entry point for the Cloud Run Job is `daily_refresh.py` (via `daily_refresh_wrapper.py`). Order:

1. **Read `data_window_end`** from the Model sheet `config` tab → compute the gap window
   `[data_window_end+1 .. refresh_date]` (Central time). Empty gap → nothing to scrape.
2. **Scrape Square** transactions for the gap (`skills/square_tips/`), cache to GCS, dedupe-append.
3. **Scrape ADP** timecards / earnings for overlapping pay periods (`skills/adp_run_automation/`).
   2FA, if challenged, goes through the **OTP gate** (see below).
4. **Mirror scrapes → raw Google Sheets** (`backfill_from_downloads.py`): `bhaga_adp_raw`,
   `bhaga_square_raw`. **Contract: downstream reads only the raw sheets, never local files.**
5. **Recompute the Model tabs** (`update_model_sheet.py`): `config, daily, labor_daily,
   labor_weekly, labor_period, tip_alloc_period, tip_alloc_daily, period_summary`
   (+ `labor_daily_forecast` via `forecast.py`).
6. **Reviews** (`process_reviews.py`): pull Google reviews from ClickUp, allocate bonuses, rebuild
   `review_bonus_period`. Idempotent on rerun.
7. **Heartbeat** success/failure DM to the BHAGA Slack channel (`notify.py`).

Per-step **idempotency markers** live in Firestore `runs/<YYYY-MM-DD>`
(`skills/bhaga_config/state_adapter.py`). A re-run skips steps already marked done. To force a step,
clear its marker — see `RUNBOOK.md` § Common tasks.

---

## Script catalog

| Script | Role |
|---|---|
| `daily_refresh.py` | **Nightly orchestrator.** Gap compute → scrape → raw → model → reviews → notify. CLI: `python3 -m agents.bhaga.scripts.daily_refresh --store palmetto [--date YYYY-MM-DD] [--skip-reviews] [--dry-run]`. |
| `daily_refresh_wrapper.py` | Thin wrapper / Cloud Run entrypoint around `daily_refresh`. |
| `otp_gate.py` | OTP **checkpoint-and-resume**: writes a pending request to Firestore + Slack, blocks until the webhook records the operator's reply. |
| `backfill_from_downloads.py` | Mirror local/GCS scrape artifacts into the canonical **raw** sheets (`_upsert_tab`, additive header migration). |
| `update_model_sheet.py` | Recompute the **Model** workbook tabs from the raw sheets. Houses the `build_*_rows` functions (one per tab). |
| `process_reviews.py` | Reviews → bonus allocation → rebuild `review_bonus_period`. |
| `forecast.py` | Builds `labor_daily_forecast` (staffing solver, guardrails, anomaly detection). |
| `notify.py` | Slack DMs under the BHAGA identity. Always DM through here, never `send_message` directly. |
| `gcs_cache.py` | Read/write scrape artifacts in GCS `bhaga-scrape-cache`. |
| `bootstrap_sheets.py` / `share_sheets_with_sa.py` | One-time: create sheets / share with the service account. |
| `verify_drilldown.py`, `verify_bq_parity.py`, `verify_against_historical_payroll.py` | Verification harnesses (parity vs historical payroll / BigQuery). |
| `backfill_bigquery.py` | Backfill raw data into BigQuery. |
| `test_*.py` | Unit tests. Run: `python3 -m pytest agents/bhaga/scripts/`. |

---

## Raw → Model data flow (the mental model)

```
Square / ADP / ClickUp  ──scrape──▶  raw Google Sheets  ──read──▶  build_*_rows()  ──upsert──▶  Model tabs
  (skills/square_tips,              (bhaga_*_raw;          (skills/tip_ledger_      (update_model_sheet.py)   (config, daily,
   adp_run_automation,              schema in             writer/reader.py)                                   labor_*, tip_alloc_*,
   ClickUp)                         tip_ledger_writer)                                                         review_bonus_period…)
```

- **Schema registry:** `skills/tip_ledger_writer/schema.py` (`WORKBOOK_SCHEMAS`) defines every tab's
  `header` + `natural_key_columns`. `get_tab_spec(workbook_title, tab_name)` returns it.
- **Writing:** `skills/tip_ledger_writer/writer.py::_upsert_tab` reads the tab, overlays incoming
  records by natural key, reconciles the header, writes back.
- **Reading raw:** `skills/tip_ledger_writer/reader.py` exposes typed readers (below).

---

## Extending the model

Two supported ways to add information. Both keep the raw → model contract intact (you never read
local files; you read raw sheets and write derived tabs).

### Raw reader catalog (`skills/tip_ledger_writer/reader.py`)

Use these to consume already-scraped data when building a derived column or tab:

| Reader | Returns |
|---|---|
| `read_raw_adp_shifts(sid)` | ADP shift rows |
| `read_raw_adp_punches(sid)` | ADP punch rows (per-punch granularity) |
| `read_raw_adp_rates(sid)` | ADP per-employee pay rates |
| `read_raw_square_transactions(sid)` | Square transaction rows |
| `read_raw_square_daily_rollup(sid)` | Square per-day rollup |
| `read_raw_square_item_daily_rollup(sid)` | Square per-item per-day rollup |
| `read_raw_kds_daily(sid)` | Square KDS per-day metrics |

(All take `account="palmetto"` by default. Resolve `sid` from the store profile, never hardcode.)

### Recipe A — add a column to an existing Model tab

Use when the new field is naturally part of an existing tab (e.g. add `tips_per_labor_hour` to
`labor_daily`).

1. **Append the column to the schema header** for that tab in
   `skills/tip_ledger_writer/schema.py` (`WORKBOOK_SCHEMAS[...]["header"]`). **Append at the end** —
   additive changes auto-migrate; reordering/renaming/removing does **not** and will raise.
2. **Emit the new value** in the matching `build_*_rows` function in `update_model_sheet.py`
   (e.g. `build_labor_daily_rows`). Produce the column in the same position you appended it.
3. **Run it.** On the next write, `_reconcile_header` detects the additive drift, widens row 1, and
   pads existing rows with blanks — **no manual sheet edit, no destructive rewrite**. (Old rows show
   blank for the new column until they're recomputed; a full backfill recomputes history.)
4. **Test** (extend the relevant `test_update_model_sheet*.py`) and verify on the sheet.

> The additive-migration contract is the whole reason adding a column is safe: see
> `_reconcile_header` / `_upsert_tab` in `skills/tip_ledger_writer/writer.py`. Only **appended**
> columns migrate automatically.

### Recipe B — create a new derived tab from raw data

Use when the new view doesn't belong on an existing tab (e.g. a `daypart_summary` tab from Square
item rollups).

1. **Register the tab** in `WORKBOOK_SCHEMAS` (`skills/tip_ledger_writer/schema.py`): pick a
   `tab_name`, define `header`, and choose `natural_key_columns` (the columns that uniquely identify
   a row, so reruns upsert instead of duplicate — e.g. `("date_local",)`).
2. **Write a `build_<tab>_rows(...)` function** in `update_model_sheet.py` that reads raw via the
   reader catalog above and returns rows aligned to your header.
3. **Wire it into `main()`** in `update_model_sheet.py` next to the other tab builds, and write it
   with the `tip_ledger_writer` upsert path (same as the existing tabs) so idempotency + header
   reconciliation apply for free.
4. **Add the sheet/tab if new workbook** — most derived tabs live in the existing Model workbook, so
   no new spreadsheet is needed. If you genuinely need a new spreadsheet, add its ID to the store
   profile `google_sheets` block (`palmetto.json`) and resolve via `resolve_sheet_id`.
5. **Test + verify + deploy.**

### After either recipe

- **Tests:** `python3 -m pytest agents/bhaga/scripts/ skills/tip_ledger_writer/`.
- **Deploy:** commit → push `main` → GitHub Actions builds/deploys the image. Local edits don't
  affect prod until deployed (`RUNBOOK.md` § Operating rules).
- **Backfill history** if the new field should be populated for past dates: re-run the model step for
  the historical window (force-rerun per `RUNBOOK.md` § Common tasks).
- **Document:** note the new column/tab in `RUNBOOK.md` § Sheet topology and add a dated line to
  `PROGRESS.md`.
