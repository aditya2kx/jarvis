# Issue #305 — ADP rerun integrity, trusted-device session reuse, pay-rate parser

**Branch:** `fix/adp-rerun-integrity-and-session-reuse`
**Issue:** [#305](https://github.com/aditya2kx/jarvis/issues/305)

Evidence tier: sandbox-live
scenario: full-live

Plus **prod-live verification** for Milestone 3 (see its Verify block).

Operator-agreed scope (2026-09-15): one branch, one PR, all defects **including** the root-cause
scrape-gate redesign. Acceptance bar for the session half is operator-set: **two consecutive prod
runs logging `restoring trusted-device session` with no 2FA challenge.**

---

## Why (incident, 2026-09-15)

Post-merge verification of #298 reran `refresh_date=2026-09-14`. The run recorded
`status=success` and loaded **zero** ADP rows.

1. Nightly `bhaga-daily-refresh-j2kdb` scraped ADP, wrote the `adp_reports` marker to Firestore, then
   failed at `load_raw_bigquery` on the FLOAT MERGE.
2. Markers persist in Firestore; the artifacts they describe land in `extracted/downloads/`
   (`daily_refresh.py:135`), container-local and never uploaded to GCS
   (`daily_refresh.py:20-37` explicitly forbids GCS as a data path).
3. Rerun `7m2h9` saw the marker, skipped ADP, found an empty directory, printed four
   `WARN: no <file> found — skipping` lines, upserted nothing, exited 0.
4. `load_raw_bigquery.done` was then written on top of the empty load.

**Root cause:** the scrape gate at `daily_refresh.py:2928` asks *"did a scrape run?"* (a Firestore
marker describing an ephemeral **action**) when the question that matters is *"is the data in
BigQuery?"* (the durable **outcome**). A marker can outlive the artifacts it describes; BQ rows
cannot. Milestone 1 closes that gap; Milestone 2 makes any residual failure loud.

---

## Invariants preserved

- **Idempotent upserts** — receipts MERGE on `(store, refresh_date, source)`; no existing merge key
  or write path changes. Re-running a date converges.
- **Integer cents / money precision** — untouched; Milestone 4 only rejects values, never rewrites.
- **America/Chicago** date boundaries — receipts are keyed by `refresh_date`, the same business date
  the markers use, never wall-clock (`state_adapter.py:3-8` contract).
- **Read-only toward ADP** — session reuse reads/writes only our own cookie jar; no Approve/Submit.
- **Sandbox isolation** — receipts go through `core.datastore.load_rows`, so
  `_assert_sandbox_write_isolation` + `BHAGA_BQ_DATASET` divert sandbox writes;
  `upload_session`/`download_session` already resolve the run's **write** bucket
  (`gcs_cache.py:160,201`). Marker clears go through `state_adapter.clear_step`, never a shell `rm`.
- **Sanctioned write layer only** — no ad-hoc BQ writes; `load_rows` everywhere.

## Feature-flag decision

Applying the "can it silently produce wrong numbers?" test:

- **Milestone 1** — no flag. The gate becomes strictly more conservative: it scrapes in exactly the
  cases it wrongly skipped before. A flag would preserve the broken path. Fail-safe by construction:
  a receipts-query error returns `False` → scrape (idempotent), never skip.
- **Milestone 2** — no flag. Converts a silent-wrong-data path into a loud failure.
- **Milestone 3** — no new flag; reuses `BHAGA_SESSION_PERSIST`, already `1` on the
  `bhaga-daily-refresh` job. Worst case is a stale jar → full login, which is today's behavior.
- **Milestone 4** — no flag. Strictly narrows what counts as a wage rate; rejection falls back to the
  earnings-derived rate.

No `FEATURE_FLAGS.md` entry required.

---

## Milestone 1 — the scrape gate consults BigQuery, not a marker

**Model routing:** Opus (schema + gate semantics; the one change that can alter scrape frequency).

### 1a. New durable load-receipt table

`source_pulls` (`core/migrations/017_source_pulls.sql:17`) is **not** usable for this: it records a
per-source *pull attempt*, so `j2kdb`'s successful ADP scrape would have written a success row and
the gate would still have skipped. The receipt must be written by the component that actually lands
rows in BQ.

New file `core/migrations/070_source_load_receipts.sql`:

```sql
-- 070_source_load_receipts.sql
-- Durable proof that a scraped export was PARSED AND UPSERTED into BQ for a
-- refresh_date. Distinct from source_pulls (017), which records a scrape
-- ATTEMPT: a scrape can succeed and set a Firestore marker while its export
-- file dies with the Cloud Run container (Issue #305). A scrape gate must
-- consult the durable sink, never the ephemeral action.
--
-- rows_upserted = 0 is a VALID receipt: a store-closed day legitimately parses
-- a timecard containing no shifts. Writing the receipt anyway is what stops the
-- gate from re-scraping every quiet day.
CREATE TABLE IF NOT EXISTS `jarvis-bhaga-prod.bhaga.source_load_receipts` (
  store         STRING    NOT NULL,
  refresh_date  DATE      NOT NULL,
  source        STRING    NOT NULL,   -- adp_timecard | adp_schedule | adp_liability | adp_rates
  rows_upserted INT64,
  loaded_at_utc TIMESTAMP,
  run_id        STRING
)
PARTITION BY refresh_date;
```

### 1b. Write a receipt at each ADP load site

In `agents/bhaga/scripts/backfill_from_downloads.py`, add beside `summaries` (line 183):

```python
def _record_load_receipt(source: str, *, store: str, refresh_date: datetime.date,
                         rows: int, dry_run: bool) -> None:
    """Durable BQ proof that <source>'s export was parsed and upserted.

    Written even when rows == 0 — a store-closed day parses a timecard with no
    shifts, and the receipt is what keeps the scrape gate from firing again the
    next night. Never raises: a receipt-write failure must not discard data that
    already landed; the gate then re-scrapes, which is idempotent.
    """
    if dry_run:
        return
    try:
        load_rows(
            "source_load_receipts",
            [{
                "store": store,
                "refresh_date": refresh_date.isoformat(),
                "source": source,
                "rows_upserted": rows,
                "loaded_at_utc": datetime.datetime.now(datetime.UTC).isoformat(),
                "run_id": os.environ.get("BHAGA_RUN_ID"),
            }],
            merge_keys=["store", "refresh_date", "source"],
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: load receipt for {source} failed (non-fatal): "
              f"{type(exc).__name__}: {exc}")
```

Call it in the `else` branch of each of the four ADP blocks, after the upserts:

| Source tag | Call site (the `else` of the `if not <file>` check) | Rows argument |
|---|---|---|
| `adp_timecard` | `backfill_from_downloads.py:190` (after line 234) | `len(shifts)` |
| `adp_schedule` | `backfill_from_downloads.py:241` | scheduled-shift rows upserted |
| `adp_liability` | `backfill_from_downloads.py:331` | liability rows upserted |
| `adp_rates` | `backfill_from_downloads.py:370` | wage-rate rows upserted |

`refresh_date` reaches this module from the existing `--window-from/--window-to` / `_in_window`
plumbing; thread the resolved business date in as `args.refresh_date` (already computed by
`daily_refresh.py` when it builds the subprocess argv at `daily_refresh.py:3206`).

### 1c. The gate requires marker **and** receipt

`agents/bhaga/scripts/daily_refresh.py:2928` today:

```python
    needs_adp = not args.skip_timecard and not step_already_done(refresh_date, "adp_reports")
```

becomes:

```python
    needs_adp = not args.skip_timecard and not (
        step_already_done(refresh_date, "adp_reports")
        and _adp_timecard_loaded(args.store, refresh_date)
    )
```

with a new helper beside `_bq_raw_coverage_complete` (`daily_refresh.py:1207`, the identical
pattern for Square):

```python
def _adp_timecard_loaded(store: str, refresh_date: datetime.date) -> bool:
    """True iff BQ holds a load receipt for this date's ADP timecard.

    The `adp_reports` Firestore marker says a scrape RAN; this says its export
    was actually parsed into BQ. Only the receipt survives the container, so
    only the receipt can safely suppress a re-scrape (Issue #305).

    Gated on the timecard alone, not all four ADP exports: the timecard is the
    one artifact every ADP run requires, while schedule is forward-looking and
    earnings/rates are pay-period cadenced behind `include_rates` — demanding
    those would re-scrape (and re-OTP) nightly.

    Returns False on any query error: re-scraping is idempotent, skipping is
    not, so False is the safe direction.
    """
    if os.environ.get("BHAGA_DATASTORE", "").lower() != "bigquery":
        return True   # non-BQ runs keep the legacy marker-only behavior
    try:
        from core.datastore import dataset, read_query

        rows = read_query(
            f"SELECT COUNT(*) AS n FROM `jarvis-bhaga-prod.{dataset()}.source_load_receipts`"
            f" WHERE store = '{store}' AND refresh_date = DATE('{refresh_date.isoformat()}')"
            f" AND source = 'adp_timecard'"
        )
        return bool(rows and int(rows[0]["n"]) > 0)
    except Exception as exc:  # noqa: BLE001
        print(f"[adp] WARN: load-receipt check failed, will re-scrape: {exc}",
              file=sys.stderr)
        return False
```

Truth table versus today:

| `adp_reports` marker | receipt in BQ | today | after |
|---|---|---|---|
| done | present | skip | **skip** (unchanged — no extra scraping) |
| done | absent | skip → **silent empty load** | **scrape** (the incident, fixed) |
| absent | — | scrape | scrape (unchanged) |

Scrape frequency is therefore unchanged except in the one broken case, which is why this does not
increase OTP exposure.

The `adp_reports` marker keeps its other readers (`daily_refresh.py:1226`
`_scrape_markers_done`, `daily_refresh_wrapper.py:203` required-steps list, `runner.py:954` per-
component tag) and `clear_adp_reports_if_shifts_missing` (`daily_refresh.py:709`) stays valid for
the Issue #267 empty-timecard case. Deliberately **not** removing the marker: it still carries
per-component granularity, and two mechanisms disagreeing is the bug class being fixed — so the
marker is retained as observability while BQ becomes authoritative for the gate.

### Verify (Milestone 1)

```bash
python3 -m pytest agents/bhaga/scripts/test_daily_refresh.py -q -k "receipt or timecard_loaded"
python3 -m pytest agents/bhaga/scripts/test_backfill_receipts.py -q
python3 scripts/check_migrations.py
```

**Pass criterion:** `_adp_timecard_loaded` returns `False` when the receipts table has no row for the
date (so `needs_adp` is `True` even with the marker done — this test fails against current `main`),
`True` when a receipt exists including `rows_upserted=0`, and `False` when `read_query` raises.
`_record_load_receipt` MERGEs on `(store, refresh_date, source)` so a second call for the same date
leaves one row, and swallows a `load_rows` exception.

---

## Milestone 2 — a load that finds nothing must fail loudly

**Model routing:** Sonnet (surgical, fully specified).

### 2a. Fix the marker names in the failure-recovery loop

`daily_refresh.py:3227` clears `("square", "adp")`, but the markers are written as
`square_transactions` (line 3131) and `adp_reports` (line 3142). **The loop has never fired.**

```python
            for _scrape_step in ("square_transactions", "adp_reports"):
```

Clearing `square_transactions` is safe and cheap: Square is OAuth REST now and never launches a
browser (`_square_will_launch_browser` returns `False`, `daily_refresh.py:2046-2048`), so a re-pull
carries no OTP cost.

### 2b. An empty ADP load must fail, not report success

`backfill_from_downloads.py:602` returns `0` unconditionally. Add before it, after the summary print
at lines 598-601:

```python
    # A load asked for ADP sources that found none of them is the
    # ephemeral-artifact failure (Issue #305). Exiting 0 here writes
    # load_raw_bigquery.done over an empty load and reports success.
    adp_requested = [s for s in ("adp_shifts", "adp_schedule", "adp_liability", "adp_rates")
                     if s not in args.skip]
    if adp_requested and not any(s in loaded_sources for s in adp_requested):
        print(
            "BREADCRUMB adp_inputs_absent — requested "
            f"{sorted(adp_requested)} but no ADP export was present in "
            f"{DOWNLOADS}; refusing to report success on an empty load"
        )
        return 1
    return 0
```

`loaded_sources` is a new `set[str]`, added beside `summaries` (line 183) and populated in the same
four `else` branches as the receipts in 1b.

Scoped to "**none** of the requested ADP sources loaded" rather than "any missing", so a genuinely
absent single export (no schedule published yet) does not fail the nightly. `BHAGA_SKIP_ADP=1` /
`--skip adp_*` paths are unaffected — `adp_requested` is then empty.

### 2c. Log the ADP marker-skip

`daily_refresh.py:3025` is `if needs_adp:` — when false, nothing is added to `pipeline_specs` and
**nothing is printed**, which is why the step was invisible in the incident log. `run_step` prints an
explicit `SKIPPED` line (`daily_refresh.py:2162`); give ADP parity:

```python
    else:
        print(
            f"\n[adp_pipeline] SKIPPED — adp_reports marker done AND BQ load "
            f"receipt present for refresh_date={refresh_date.isoformat()}"
        )
```

### Verify (Milestone 2)

```bash
python3 -m pytest agents/bhaga/scripts/test_backfill_adp_required.py -q
python3 -m pytest agents/bhaga/scripts/test_daily_refresh.py -q -k "marker or scrape_clear"
```

**Pass criterion:** `main()` returns `1` when all four ADP inputs are absent and `--skip` does not
exclude them; returns `0` when `--skip` excludes all four; returns `0` when at least one ADP export
loaded. A test asserts the recovery loop clears exactly `square_transactions` + `adp_reports` — it
fails against the current `("square", "adp")` tuple.

---

## Milestone 3 — ADP trusted-device session reuse (no more OTP prompts)

**Model routing:** Opus (live browser/session interaction, prod-verified).

### Current state — the mechanism is inert end to end

- `upload_session` / `download_session` / `delete_session` (`gcs_cache.py:160,177,201`) have **zero
  callers** outside tests and docs.
- `launch_persistent` accepts `storage_state` and restores it at `runtime.py:368-370`, but **no
  caller passes it** (all 11 call sites checked), and nothing ever calls `context.storage_state()`.
- `gs://bhaga-scrape-cache/_session/` holds only a stale `square-palmetto.json` (2026-06-23); no ADP
  object has ever existed; `restoring trusted-device session` has appeared **0** times in 45 days.
- Over those 45 days ADP logins ran ~daily and 2FA challenged 3 times (09-02, twice on 09-15), so
  ADP's risk engine is sporadic — but with no trusted device we are fully exposed each time it fires.

`.cursor/rules/bhaga.mdc` § Operational rules already *claims* "most nights the trusted-device
session clears it silently". This milestone makes that true.

### 3a. Wire restore + save around the ADP bundle session

The nightly's single ADP login is `launch_persistent(portal="adp", ...)` at
`skills/adp_run_automation/runner.py:2134`, with `_ensure_logged_in(page, store=store)` at line 2140.

Add to `runner.py`:

```python
_SESSION_LOCAL = DOWNLOADS_DIR.parent / "adp-session.json"


def _session_persist_enabled() -> bool:
    return os.environ.get("BHAGA_SESSION_PERSIST", "").strip() in ("1", "true", "yes")


def _restore_adp_session(*, store: str) -> str | None:
    """Local path to a restored ADP storage_state, or None for a fresh jar."""
    if not _session_persist_enabled():
        return None
    from agents.bhaga.scripts.gcs_cache import download_session
    _SESSION_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    if download_session(_SESSION_LOCAL, portal="adp", store=store):
        return str(_SESSION_LOCAL)
    return None


def _persist_adp_session(ctx, *, store: str) -> None:
    """Save the post-login cookie jar so the next run is a trusted device.

    Called after every successful login, including one that satisfied a 2FA
    challenge — that is precisely the run whose jar carries ADP's device-trust
    cookie. Never raises: losing a session costs one extra login and must not
    fail a run whose data already landed.
    """
    if not _session_persist_enabled():
        return
    try:
        from agents.bhaga.scripts.gcs_cache import upload_session
        ctx.storage_state(path=str(_SESSION_LOCAL))
        upload_session(_SESSION_LOCAL, portal="adp", store=store)
    except Exception as exc:  # noqa: BLE001
        print(f"[adp_bundle] WARN: session persist failed (non-fatal): "
              f"{type(exc).__name__}: {exc}")
```

Then at `runner.py:2134-2140`:

```python
    with launch_persistent(
        portal="adp",
        headed=headed,
        slow_mo_ms=slow_mo_ms,
        keep_open_on_error=keep_open_on_error,
        storage_state=_restore_adp_session(store=store),
    ) as (ctx, page):
        _ensure_logged_in(page, store=store)
        _persist_adp_session(ctx, store=store)
```

Saving immediately after `_ensure_logged_in` rather than at block exit means the jar is captured even
if a later component (timecard, schedule, liability) fails, so a partial run still buys the next run
a trusted device.

### 3b. Stale jar handling

If a restored session is present **and** ADP still challenges, the jar is stale;
`_persist_adp_session` overwrites it after the successful OTP, so the next run gets the fresh one. No
`delete_session` call is needed on this path — reserve it for a hard login failure with a restored
jar, mirroring the Square anti-bot precedent (`bhaga.mdc` § Recovery).

### Verify (Milestone 3)

```bash
python3 -m pytest skills/adp_run_automation/test_session_persist.py -q
```

Unit pass criterion: `_restore_adp_session` returns `None` when `BHAGA_SESSION_PERSIST` is unset and
when `download_session` misses; returns the local path on a hit. `_persist_adp_session` calls
`ctx.storage_state(path=...)` then `upload_session`, and swallows an exception from either.

**Prod-live pass criterion (operator-set):** two consecutive prod runs log
`[runtime] adp: restoring trusted-device session` and contain **no** `[adp 2fa] detected challenge`:

```bash
gcloud logging read 'resource.type="cloud_run_job" textPayload:"restoring trusted-device session"' \
  --project=jarvis-bhaga-prod --freshness=2d --format="value(timestamp,textPayload)"
gcloud logging read 'resource.type="cloud_run_job" textPayload:"2fa] detected challenge"' \
  --project=jarvis-bhaga-prod --freshness=2d --format="value(timestamp)"
gsutil ls -l gs://bhaga-scrape-cache/_session/adp-palmetto.json
```

The first post-merge run is expected to still challenge once — it seeds the jar — so runs 2 and 3
are the evidence. The `_session/adp-palmetto.json` timestamp must advance on each run.

---

## Milestone 4 — `parse_hourly_pay_rate` must not attribute an arbitrary page number

**Model routing:** Sonnet (pure-function change + unit tests).

### 4a. Remove the loose fallback

`skills/adp_run_automation/pay_info_backend.py:79-82`:

```python
        m2 = re.search(r"\$?\s*([\d,]+\.\d{2,4})", blob)
        if m2 and ("hour" in blob.lower() or "pay" in blob.lower() or blob.strip().startswith("$")):
```

This takes the **first decimal number anywhere in the blob**, gated only on the blob containing
"hour" or "pay" — true of every ADP payroll page. On 2026-09-15 it returned `1.25` for three
employees (Browning 15.25→1.25, Garcia 15.25→1.25, Krause 25.00→1.25); an identical value across
three people is a constant page artifact, not three real rates. The comment at line 679 attributes
1.25 to a salaried "token hourly", but Browning and Garcia are not salaried.

Delete the `m2` branch. Keep the strict `_HOURLY_RATE_RE` (line 33) and the label-proximity `m3`
regex (lines 84-90), which bounds the match to within 40 characters of `Hourly pay rate`. With
neither matching, the existing `raise ValueError("Hourly pay rate not found on Payroll info page")`
(line 92) fires — a per-employee failure that captures evidence and leaves the rate untouched, so
the employee keeps their earnings-derived rate.

Retain the one case `m2` legitimately covered — an `<input>` whose entire value is the rate — with an
anchored pattern:

```python
        if re.fullmatch(r"\$?\s*([\d,]+\.\d{2,4})\s*", blob):
            rate = float(blob.strip().lstrip("$").replace(",", ""))
            break
```

`fullmatch` means the blob must be *only* a number, so it can never pick a stray figure out of prose.

### 4b. Widen the guard from "50% drop" to a plausibility band

`pay_info_backend.py:680` refuses only `new_f < 0.5 * old_f`. A spurious parse between 50% and 100%
of the previous rate — or above it — is written silently. With this roster clustered at 15.25–16.25,
a bogus `16.25` would land unnoticed.

```python
_RATE_FLOOR_DOLLARS = 7.25    # federal minimum; no Palmetto rate is legitimately below this
_RATE_CEILING_DOLLARS = 100.0

            implausible = not (_RATE_FLOOR_DOLLARS <= new_f <= _RATE_CEILING_DOLLARS)
            if old_f > 0 and (implausible or new_f < 0.5 * old_f or new_f > 2.0 * old_f):
                print(
                    f"[pay_info] BREADCRUMB refused_rate_drop name={key} "
                    f"old={old_f} new={new_f}"
                )
                continue
```

Breadcrumb text unchanged so existing log greps keep working.

### Verify (Milestone 4)

```bash
python3 -m pytest skills/adp_run_automation/test_pay_info_parse.py -q
```

**Pass criterion:** `parse_hourly_pay_rate` raises `ValueError` on a body containing `1.25` as an OT
multiplier with no `Hourly pay rate` label (today it returns `1.25`); still returns `15.25` for the
real `Hourly pay rate $15.25` layout and for a bare input value `"15.25"`; the guard refuses
`15.25 → 1.25`, `15.25 → 40.00`, `15.25 → 5.00` and accepts `15.25 → 16.25`.

---

## Whole-PR verification

```bash
python3 scripts/verify.py --full
python3 scripts/check_doc_freshness.py
python3 agents/bhaga/scripts/sandbox_e2e.py --scenario full-live
```

Reusing the existing `full-live` scenario rather than adding one — it already exercises the ADP
bundle → `load_raw_bigquery` path this PR changes (preference J5).

## Docs lock-step

| File | Change |
|---|---|
| `RUNBOOK.md` | §13 force-rerun: the scrape gate is now marker **AND** BQ receipt, so forcing a re-scrape means deleting the `source_load_receipts` row (clearing `adp_reports` alone no longer suffices), and downstream `load_raw_bigquery`/`materialize_model_bq`/`process_reviews` markers must also be cleared or re-scraped files are discarded. Add `_session/adp-palmetto.json` troubleshooting. |
| `agents/bhaga/scripts/README.md` | Correct the marker names in the recovery description (line 47 documents the wrong `square`/`adp` names); document `source_load_receipts` and the new `adp_inputs_absent` failure mode. |
| `.cursor/rules/bhaga.mdc` | § Architecture: state that ADP scrape idempotency is BQ-receipt-authoritative, Firestore marker advisory. § Operational rules: the trusted-device claim becomes true only with `BHAGA_SESSION_PERSIST` + the `_session/` object. |
| `agents/bhaga/scripts/status.py` | Add `source_load_receipts` to the freshness targets so `status` answers "did ADP land?" directly. |
| `PROGRESS.md` | Dated entry for the incident + fix. Lands via its own follow-up PR, never a direct main push. |

## Branch / PR mechanics

One branch, one PR: `fix/adp-rerun-integrity-and-session-reuse` → `gh pr create --base main`. All
GitHub ops as `jarvis-agent-bot328`. Never self-merge; reply to every review thread; one push per
review cycle per the babysit batch loop. `python3 scripts/verify.py --full` before pushing.

## Out of scope (follow-up)

**Duplicate identities**: `Huynh, Hillary` and `Huynh Hillary` both exist in `adp_wage_rates` with
`rate_source=pay_info` — name-normalization dupe, its own issue.
