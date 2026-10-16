# Issue #401 — Operator Console survives deploys for open tabs (stale Server Action)

Jam + §4 approved in chat 2026-10-16 ("go") and recorded via `phase_state.py advance --operator-approved`.

Evidence tier: unit-only
waiver: The failure only reproduces across two prod deploys with a tab left open. Pre-merge proof is a local Turbopack build comparison (same key → same action id) plus a unit test of the reload copy; post-merge proof compares the action manifest of two consecutive prod images.

## Root cause

2026-10-16 21:33Z, revision `operator-console-00132-d8d` (deployed 21:30:36Z, PR #400) logged `Failed to find Server Action "60337d2b…"` three times — the operator's Labor tab was loaded on 00131. Next 16 salts every Server Action id with `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` (`node_modules/next/dist/build/webpack-config.js` line 417 `serverReferenceHashSalt: encryptionKey`); unset, `generateEncryptionKeyBase64` (`next/dist/server/app-render/encryption-utils-server.js` line 96) makes a random key per build. `.github/workflows/operator-console-deploy.yml` line 41 builds with neither that key nor `NEXT_DEPLOYMENT_ID` (only the preview workflow line 72 sets the deployment id), so every deploy invalidates every open tab's buttons. No `labor_schedule_pushes` rows were written (BQ checked), so no ADP side effect.

Measured locally (Turbopack, Next 16.2.10): two builds with the same key → `saveDraftsToAdpAction` id `60e0ea11…` both times; two keyless builds → `6074881c…` and `60070432…`. Same key with `NEXT_DEPLOYMENT_ID=sha-aaa` vs `sha-bbb` → identical ids.

## Invariant

No server action body changes. ADP stays read-only except the existing operator-clicked draft/publish path (bhaga.mdc invariant 6). The key never lands in git or logs: Secret Manager `operator-console-server-actions-key` (created 2026-10-16, 32 random bytes, project `jarvis-bhaga-prod`), read in the deploy job, `::add-mask::`ed, passed as a builder-stage `ARG` only (the pushed `runner` stage does not carry build args). Idempotent upserts, integer cents, America/Chicago untouched.

Feature flag: no new flag. This cannot silently produce wrong numbers — it changes action id hashing and an error string only (`docs/FEATURE_FLAGS.md` unchanged). Out of scope: direct action callers outside `useConsoleAction` (e.g. `SyncAdpButton` polling) and key rotation tooling.

## UX

No new component. `useConsoleAction` already shows errors through the shared `ActionToast` (`lib/actions/ActionToast`) and the inline `error` state. A stale action now reads "The console was updated since this page loaded — reload the page to continue. Nothing was saved." No auto-reload (would drop unsaved editor rows). Hover/pending/disabled states unchanged.

## Milestone 1 — stable ids + prod deployment id (Composer)

`apps/operator-console/Dockerfile` builder stage after line 15:

```dockerfile
ARG NEXT_SERVER_ACTIONS_ENCRYPTION_KEY=""
```

`.github/workflows/operator-console-deploy.yml` "Build image" step:

```bash
KEY=$(gcloud secrets versions access latest --secret=operator-console-server-actions-key --project=${{ env.PROJECT_ID }})
echo "::add-mask::$KEY"
docker build --build-arg "NEXT_DEPLOYMENT_ID=${{ github.sha }}" --build-arg "NEXT_SERVER_ACTIONS_ENCRYPTION_KEY=$KEY" -t … -t … .
```

WIF SA `bhaga-orchestrator@` already holds project-level `roles/secretmanager.secretAccessor`.

**Verify:** `cd apps/operator-console && K=$(openssl rand -base64 32); for i in 1 2; do rm -rf .next; NEXT_SERVER_ACTIONS_ENCRYPTION_KEY=$K npx next build; python3 -c "import json;m=json.load(open('.next/server/server-reference-manifest.json'));print([k for k,v in m['node'].items() if v.get('exportedName')=='saveDraftsToAdpAction'])"; done` → same id twice.

## Milestone 2 — clear stale-action copy (Composer)

`apps/operator-console/lib/actions/useConsoleAction.ts` catch block (line 59–64): if `unstable_isUnrecognizedActionError(e)` (exported by `next/navigation`), use `STALE_ACTION_MESSAGE` instead of `e.message`.

```ts
export const STALE_ACTION_MESSAGE =
  "The console was updated since this page loaded — reload the page to continue. Nothing was saved.";
```

Test `apps/operator-console/__tests__/use-console-action.test.tsx`: `run(() => Promise.reject(new UnrecognizedActionError("Server Action \"x\" was not found on the server.")))` → `ack.error === STALE_ACTION_MESSAGE` and toast pushed with it; a plain `Error("boom")` still surfaces "boom".

**Verify:** `cd apps/operator-console && npx vitest run __tests__/use-console-action.test.tsx`

## Milestone 3 — docs lock-step (Composer)

`docs/operator-console/ARCHITECTURE.md` (deploy section) and `RUNBOOK.md` §17: stable Server Action key secret, `NEXT_DEPLOYMENT_ID=<sha>`, rotating the key invalidates open tabs once.

**Verify:** `python3 scripts/check_doc_freshness.py`

## Scenarios

Happy: unchanged action on a tab opened before a deploy → succeeds (same id). Changed/removed action on an old tab → reload copy, nothing executed. Legacy: tabs opened on 00132 (random key) fail once after this PR deploys → reload copy. Plain server errors → unchanged message.

## PR mechanics

Branch `fix/console-stale-server-action`, one PR `--base main`, `Closes #401`, bot `jarvis-agent-bot328`, never self-merge, reply to every thread, babysit per `pr-workflow.mdc`. Post-merge: compare `saveDraftsToAdpAction` id in this PR's prod image vs the next deploy's image.

Model routing: Composer for all milestones; no Opus plan review.
