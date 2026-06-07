# Post-merge cutover — BQ single source of truth (PR #33)

This is the operator checklist to run **after PR #33 merges to `main`** and the new
orchestrator image is deployed. It turns the BQ-single-source-of-truth design on in **prod**
and retires the latent `square_item_lines` double-count discovered during verification.

> Authoritative operational home: **`RUNBOOK.md` § 15**. This file is the standalone, ordered
> runbook for the one-time cutover; the RUNBOOK sections it links are the source of truth for each
> individual command. Everything here runs as the orchestrator SA
> (`bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com`), reading the cloud — never a laptop.

---

## TL;DR

| Step | What | Wipe? | OTP? | One-time? |
|---|---|---|---|---|
| 1 | Apply migrations 004–007 | no | no | yes |
| 2 | Seed `bhaga.store_config` from the Sheet | no | no | yes |
| 3 | **One-time full-history `--replace` rebuild of raw BQ** (fixes `item_lines` dupes) | **raw tables only, once** | **yes** | yes |
| 4 | Verify parity (BQ ⇆ prod Sheets) from the first data date | no | no | yes |
| 5 | Flip `BHAGA_SHEET_FROM_BQ=1` (BQ-canonical model) after reconcile is green | no | no | yes |

After this, **nightly is normal incremental MERGE — no wipe ever again** (see "Why no daily wipe").

---

## Why a one-time rebuild — and why nightly never wipes again

The verification found `square_item_lines` inflated ~2× in prod (e.g. one ticket line stored twice
with `line_seq` `0` **and** `3500`). Root cause: an **older parser used a file-global row index** for
`line_seq`; the current parser assigns a **stable per-group counter**
(`skills/square_tips/transactions_backend.py`, guarded by `LineSeqExportStabilityTests`). Because
`line_seq` is part of the merge key `(transaction_id, item_name, item_sold_at_local, line_seq)`, the
legacy `…|3500` rows and the new `…|0` rows never collided, so re-scrapes accumulated duplicates.

- **The instability is already fixed in code** — the current parser can only emit small per-group
  indices (`0`, `1`, …), never `3500`. So new dupes cannot be created.
- **Step 3 is a one-time cleanup** of the historical artifact: a full-history TRUNCATE-then-load
  rebuilds every raw row with a consistent per-group `line_seq`. This is the exact path validated in
  the sandbox (`bhaga_sandbox` → correct 6586 vs prod's inflated 8435).
- **Nightly stays MERGE-only** (`backfill_from_downloads` without `--replace`): it scrapes only the
  coverage gap (`bq_coverage`) and upserts by natural key. With `line_seq` now stable, an overlapping
  re-scrape produces identical keys → in-place update, never a duplicate.
- **The model self-heals** — `materialize_model_bq` recomputes from BQ raw and MERGE-upserts by
  date/period keys, so item metrics become correct on the first nightly run after Step 3. No model wipe.

---

## Step 1 — Apply migrations 004–007

```bash
BHAGA_DATASTORE=bigquery \
BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
  python3 -c "from core.datastore import ensure_schema; print(ensure_schema())"
```

Idempotent (`CREATE … IF NOT EXISTS`). Expected: the not-yet-applied versions on first run, `[]` after.
See RUNBOOK § "Running SQL migrations".

## Step 2 — Seed `bhaga.store_config` from the current Sheet values

One-time, idempotent. Full snippet in **RUNBOOK § 15 → "Seed the BQ config from the current Sheet
values"**. After seeding, the Sheet config tab is display-only; the pipeline reads `store_config`
(BQ-first, Sheet fallback).

## Step 3 — One-time full-history `--replace` rebuild of raw BQ (the dedupe)

This is the only "wipe", and it is **raw tables only, once**. It must cover the **entire history**
(first data date → today): a windowed `--replace` would TRUNCATE and then only reload the window,
dropping out-of-window rows. The first Square data date is **2026-03-23**; ADP pay-period pulls reach
back to ~2026-02-16 automatically.

Run as a **Cloud Run one-off job** (per RUNBOOK § 13 → "Run a one-off backfill against prod"), using
the unified window + replace + BQ-canonical model env knobs — i.e. the same combination the
`full-history-bq-sandbox` scenario used, but pointed at the prod `bhaga` dataset:

```
BHAGA_DATASTORE=bigquery
BHAGA_WINDOW_FROM=2026-03-23      # data_window_start; full history
BHAGA_WINDOW_TO=<last closed day>
BHAGA_RAW_REPLACE=1              # TRUNCATE-then-load each raw table (fresh scrape owns it)
BHAGA_SHEET_FROM_BQ=1           # compute the model FROM BQ, render Sheet as projection
# NOTE: do NOT set BHAGA_BQ_DATASET — prod cutover writes the default `bhaga` dataset.
```

This **scrapes all sources live** (Square + ADP timecard + ADP earnings custom range + reviews) →
TRUNCATE-then-loads raw BQ → materializes the model → renders the Sheets. **It needs OTP** (Square +
ADP) via the normal READY/OTP Slack handshake — be available to answer.

Caveats:
- `--replace` truncates **all** raw tables, not just `item_lines`. That's safe **only** because the
  full-history window re-scrapes everything; never run `--replace` with a partial window against prod.
- This is also the canonical way to fill the `adp_earnings` history (prod was pinned to "last payroll";
  the unified window pulls the full earnings range).

## Step 4 — Verify parity (BQ ⇆ prod Sheets), full dataset

```bash
BHAGA_DATASTORE=bigquery BHAGA_SECRETS_BACKEND=gcp \
BHAGA_IMPERSONATE_SA=bhaga-orchestrator@jarvis-bhaga-prod.iam.gserviceaccount.com \
  python3 -m agents.bhaga.scripts.verify_prod_parity --store palmetto --from 2026-03-23
```

Expected after the rebuild:
- RAW: `transactions`, `daily_rollup`, `item_daily_rollup`, `kds_daily`, `reviews` exact; **`item_lines`
  now matches** the deduped count (the prod Sheet still reflects the old dupes until its projection is
  re-rendered from BQ in Step 3, so compare against BQ / a freshly-rendered Sheet).
- MODEL: grains key-join; remaining diffs limited to representation (`over_saturation` `ok`/`over` vs
  boolean, `$` formatting) and `N/A → value` where the earnings backfill added coverage.

A backfill isn't done until it's verified (RUNBOOK § 13 → "Verify after a backfill").

## Step 5 — Flip `BHAGA_SHEET_FROM_BQ=1` permanently

If you ran Step 3 as a one-off it set the flag for that run only. To make the **nightly**
`bhaga-daily-refresh` job BQ-canonical, follow **RUNBOOK § "Flip procedure (`BHAGA_SHEET_FROM_BQ`)"**:
confirm `reconcile_model` green ≥ 2 nights, set the env var on the job, run a manual job, record it in
`docs/FEATURE_FLAGS.md`.

---

## Alternative to Step 3 — targeted dedup (no re-scrape, no OTP)

If you'd rather not re-scrape all of history, the `item_lines` dupes can be removed in place by
deleting the legacy file-global-`line_seq` rows (the ones whose `line_seq` exceeds the real per-group
count). This avoids OTP but is **not** validated end-to-end the way the `--replace` rebuild is, and it
does **not** fill the `adp_earnings` history. Prefer Step 3 unless minimizing scrape/OTP load matters;
if you take this path, snapshot the table first and verify counts per `(transaction_id, item_name,
item_sold_at_local)` group against a fresh export before/after.

---

## Rollback / safety notes

- All raw/model writes are idempotent upserts; re-running any step converges, never duplicates.
- The sandbox dataset (`bhaga_sandbox`) is isolated by `_assert_sandbox_write_isolation`; the prod
  cutover deliberately leaves `BHAGA_BQ_DATASET` unset to write the default `bhaga` dataset.
- GCS is **not** in the data path (sessions + evidence only) — there is nothing to restore from GCS and
  nothing to clean up there.
