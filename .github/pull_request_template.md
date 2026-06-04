<!--
PURPOSE OF THIS DESCRIPTION
The operator reads this to decide whether to approve — without asking follow-up
questions. It must answer:
  • What exactly changed and why?
  • Does it work end-to-end? (Prove it with real output, not "it should work".)
  • Is it backward compatible? (Prove it — diff, flag default, test run.)
  • Will it cause a regression? (Show the legacy path still passes.)

Keep it concise. Diagrams (Mermaid, ASCII, screenshots) are strongly preferred
over paragraphs when they communicate structure or flow.

Fill in every section. CI will FAIL if any section is missing or left as
placeholder text. Delete these HTML comments before submitting.
-->

## 1. What is the change
<!-- Concrete change in 2–5 sentences: what was added/modified/removed and where. -->


## 2. Motivation
<!-- Why this change exists. Problem it solves or capability it adds.
     Link the ticket / chat session / PROGRESS.md entry. -->


## 3. End-to-end test (with evidence)
<!-- How you verified this works end-to-end — not just unit tests.
     Paste the REAL commands you ran and the REAL output / sheet diff / log excerpt.
     "It should work" or "tests pass" alone is not evidence.
     For BHAGA: show the affected sheet/tab before→after or the job log + a row spot-check.
     Diagrams showing the data flow or before/after state are encouraged. -->

<details><summary>Evidence</summary>

```
<paste real commands + real output here>
```

</details>


## 4. Backward compatibility — and proof
<!-- State explicitly: yes or no, then PROVE it.
     - New behavior behind a flag? Name it + its default.
     - Schema change additive only? Show the header diff.
     - Existing paths / nightly unaffected? Show the legacy test run or log.
     If NOT backward compatible, say so and describe the migration. -->


## 5. Checklist
- [ ] Tests added/updated and passing (`python3 -m pytest agents/bhaga/scripts/ skills/ core/ cloud/`)
- [ ] Docs updated in lock-step (`python3 scripts/check_doc_freshness.py` is clean)
- [ ] No secrets / PII in the diff (credentials live in Secret Manager / Keychain)
- [ ] Cloud paths read from GCS, not laptop `extracted/downloads/`
- [ ] Money math is Decimal-precise; writes are idempotent (upsert by natural key)
