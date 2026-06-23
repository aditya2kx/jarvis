# Dogfood evidence

This directory holds **end-to-end lifecycle conformance runs** produced by
`scripts/dogfood_lifecycle.py`. Each `lifecycle-run-<date>.md` is an annotated
transcript proving that a dummy requirement walked all 12 substeps of the Jarvis
lifecycle (`scripts/lifecycle.py`) against **real** infrastructure — a real
tracking issue, a throwaway PR off `origin/main`, real operator-gate enforcement,
and a real operator merge.

> This README is permanent documentation. It is **not** the same as the throwaway
> `DOGFOOD_DOC` stub inside `scripts/dogfood_lifecycle.py` (lines 573–588), which is
> the doc the harness writes during a mechanical dummy run.

## What the orchestrator does

`dogfood_lifecycle.py` drives a trivial requirement ("Add docs/dogfood/README.md")
through every lifecycle substep, pausing at the operator-reserved `merge` gate
because the bot is a non-admin and branch protection forces a human approval.

## Step-marker annotation types

Each substep in a transcript is tagged with one of four markers:

| Marker | Meaning |
|---|---|
| `SEEDED` | Substep was factually complete the moment `new_requirement` ran. |
| `HARNESS-DRIVEN` | The agent/harness drove this substep autonomously. |
| `OPERATOR-SIMULATED` | Harness simulated operator approval to demo a gate. |
| `OPERATOR-REAL` | A real operator merge (the one requiring your GitHub click). |

## Subcommands

| Command | What it does |
|---|---|
| `run` | Walks the 8 agent substeps + 2 gate demos, opens the dummy PR, pauses at `merge`. |
| `resume` | After the operator merges the dummy PR: merge + post-merge-verify + retrospective; writes the transcript. |
| `check` | Offline, deterministic conformance assertions on the recorded run (safe in CI). |
| `cleanup` | Removes the state file / tears down the dummy run. |

## State file

Run state is persisted at `metrics/pr_cost/dogfood-state.json` (gitignored). It is
read by `resume`, `check`, and `cleanup`.

## Reproduce a run

```bash
python3 scripts/dogfood_lifecycle.py run        # opens dummy PR, pauses at merge gate
# operator approves + squash-merges the dummy PR, then:
python3 scripts/dogfood_lifecycle.py resume      # finishes lifecycle, writes transcript
python3 scripts/dogfood_lifecycle.py check       # offline conformance assertions
python3 scripts/dogfood_lifecycle.py cleanup     # remove state when done
```

## See also

- `docs/WORKFLOW.md §7` — Verification matrix; the dogfood guarantee row lives here.
- `scripts/dogfood_lifecycle.py` — the orchestrator (module docstring documents the flow).
