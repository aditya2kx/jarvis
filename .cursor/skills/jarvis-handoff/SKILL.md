---
name: jarvis-handoff
description: Hand this PR's work to the other surface — Mac ↔ Cursor Cloud Agent (phone/web) — with a note so the next agent picks up where this one stopped.
disable-model-invocation: true
---

# jarvis-handoff

The operator is switching device mid-PR: Mac → phone (`/jarvis-handoff cloud`) or phone → Mac
(`/jarvis-handoff mac`). Chat history does not travel, so the handoff note is the next agent's
whole context. Canonical: `docs/contributing/dev-anywhere.md` § Handoff.

## What to do

1. Target = the word after `/jarvis-handoff` (`cloud` or `mac`). If none: `cloud` when
   `python3 scripts/gcp_access_probe.py` prints `dev_surface=mac_local`, else `mac`.
2. Commit everything (a `wip:` commit is fine) and push the branch. On a Cloud Agent run
   `bash scripts/cloud_git_identity.sh` first so the push lands as `jarvis-agent-bot328`.
   No PR yet? Open it the surface's normal way first (pr-workflow.mdc step 2).
3. Write the note to a temp file — short, for an agent reading it cold:
   - **State:** what works, what was verified (commands + results), last green CI.
   - **Next:** the concrete next steps, in order.
   - **Open:** unanswered review threads, operator questions pending, gates not yet approved.
   - **Gotchas:** anything you learned that isn't in the repo.
4. Run verbatim:

```bash
python3 scripts/handoff.py out --pr <N> --to <cloud|mac> --note-file <file>
```

5. Tell the operator where to continue:
   - `cloud`: a Cloud Agent starts on this PR's branch; its link is commented on the PR in about a
     minute (open it from Cursor mobile/web, or the Cloud Agents panel).
   - `mac`: in any Jarvis chat on the Mac, run `python3 scripts/handoff.py in --pr <N>` — it makes
     the worktree, restores phase tracking and opens a Cursor window seeded to continue.
6. Then **stop**: no more pushes, comments or babysitting from this surface. On a Cloud Agent,
   unsubscribe from the PR's events.

## Hard rules

- Never hand off with uncommitted or unpushed work — `handoff.py` refuses; fix it, don't bypass.
- Never put secrets or prod data in the note (the repo is public).
- One owner at a time: after `out`, this surface is done until a handoff comes back.
