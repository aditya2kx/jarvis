<!--
PURPOSE OF THIS DESCRIPTION
The operator reads this to decide whether to approve — without asking follow-up
questions. It must answer:
  • What exactly changed and why?
  • How was it designed? (Architecture, data flow, key decisions — diagrams preferred.)
  • Does it work end-to-end? (Prove it with real output, not "it should work".)
  • Is it backward compatible and regression-free? (Prove it.)

Keep it concise. Diagrams (Mermaid, ASCII, screenshots) are strongly preferred
over paragraphs for architecture and data flow.

Fill in every section. CI will FAIL if any section is missing or left as
placeholder text. Delete these HTML comments before submitting.
-->

## 1. What is the change
<!-- Concrete change in 2–5 sentences: what was added/modified/removed and where. -->


## 2. Motivation
<!-- Why this change exists. Problem it solves or capability it adds.
     Link the ticket / chat session / PROGRESS.md entry. -->


## 3. Design / Approach
<!-- How the change is structured: key design decisions, data flow, component interactions.
     Diagrams are strongly preferred — Mermaid blocks, ASCII art, and screenshots all render in GitHub.
     For non-trivial changes: why this approach over alternatives? What did you rule out and why? -->


## 4. End-to-end test (with evidence)
<!-- How you verified this works end-to-end.
     REQUIRED: paste the REAL commands you ran AND the REAL output they produced.
     "Tests pass" alone is NOT evidence — it will FAIL CI.
     Unit test output (pytest PASSED/FAILED lines) counts only as a supplement,
     not as the primary evidence. Show what the TOOL actually does when run:
       - For a new script: run it and paste its output (real data, real rows).
       - For a pipeline change: paste the job log or the sheet before→after.
       - For a BHAGA change: show the sandbox e2e output or a real model tab spot-check.
     CI will reject evidence that consists ONLY of pytest output. -->

<details><summary>Evidence</summary>

```
<paste real commands + real output here>
```

</details>

<!-- OPTIONAL — for changes that have observable post-merge state to verify.
     The pr-merged-lifecycle.yml workflow will automatically run READ-ONLY
     commands from this block after the PR is merged and post results as an
     issue comment.  Side-effecting commands (scrape, deploy, OTP, gcloud run)
     are listed as "agent follow-up" items and NOT auto-run.
     Omit this section entirely if there is nothing meaningful to verify post-merge.
-->
### Post-merge verification
<!--
```bash
# Example read-only commands (replace or remove as appropriate):
# python3 -m agents.bhaga.scripts.status --store palmetto
# gh pr view <n> --json state,mergedAt
# python3 scripts/phase_state.py status
```
-->


## 5. Backward compatibility — and proof
<!-- State explicitly: yes or no, then PROVE it.
     - New behavior behind a flag? Name it + its default.
     - Schema change additive only? Show the header diff.
     - Existing paths / nightly unaffected? Show the legacy test run or log.
     - No regression? State which tests cover the affected path and that they pass.
     If NOT backward compatible, say so and describe the migration. -->


## 6. Checklist
- [ ] Tests added/updated and passing (`python3 -m pytest agents/bhaga/scripts/ skills/ core/ cloud/`)
- [ ] Docs updated in lock-step (`python3 scripts/check_doc_freshness.py` is clean)
- [ ] No secrets / PII in the diff (credentials live in Secret Manager / Keychain)
- [ ] Cloud paths read from GCS, not laptop `extracted/downloads/`
- [ ] Money math is Decimal-precise; writes are idempotent (upsert by natural key)
