<!--
Fill in every section. The Claude reviewer bot and reviewers will REQUEST CHANGES
if any section is missing or left as placeholder text. Delete these comments.
-->

## 1. What is the change
<!-- The concrete change in 2–5 sentences. What was added/modified/removed and where. -->


## 2. Motivation
<!-- Why this change exists. The problem it solves or the capability it adds. Link the
ticket / chat / PROGRESS.md entry if there is one. -->


## 3. End-to-end test (with evidence)
<!-- How you verified this works END TO END, not just unit tests. Paste the command(s)
you ran and the real output / sheet diff / log excerpt. "It should work" is not evidence.
For BHAGA: show the affected sheet/tab before→after, or the job/backfill log + a row spot-check. -->

<details><summary>Evidence</summary>

```
<paste commands + output here>
```

</details>


## 4. Backward compatibility — and proof
<!-- Is this backward compatible? State explicitly. Then PROVE it:
- New behavior behind a feature flag? Name the flag + its default (off).
- Schema change additive only (no column reorder/removal)? Show the header diff.
- Existing tabs / consumers / nightly daily_refresh unaffected? Show the legacy path still passes
  (e.g. the existing test suite green, or a legacy-regression run).
If it is NOT backward compatible, say so loudly here and describe the migration. -->


## 5. Checklist
- [ ] Tests added/updated and passing (`python3 -m pytest agents/bhaga/scripts/ skills/ core/ cloud/`)
- [ ] Docs updated in lock-step (`python3 scripts/check_doc_freshness.py` is clean)
- [ ] No secrets / PII in the diff (credentials live in Secret Manager / Keychain)
- [ ] Cloud paths read from GCS, not laptop `extracted/downloads/`
- [ ] Money math is Decimal-precise; writes are idempotent (upsert by natural key)
