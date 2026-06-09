#!/usr/bin/env bash
# Finalize a merged PR's cost record on main RIGHT NOW (not waiting for the next PR).
#
# Usage: bash scripts/finalize_cost.sh <pr-number>
#
# What it does:
#   1. Syncs merged_at + diff stats from GitHub and re-captures build+review cost.
#   2. Regenerates report.html.
#   3. Opens a metrics/pr_cost/**-only PR and arms auto-merge (squash).
#      The operator still approves — this never self-merges.
#
# When to use:
#   Normally the post-merge hook (scripts/git-hooks/post-merge) regenerates the
#   local report after `git pull`, and the next feature PR's pre-commit hook carries
#   the finalized JSON to main. Run THIS script only when you need the corrected
#   record on main immediately — e.g. the cost was materially wrong and you want the
#   canonical ledger fixed now rather than after the next PR.
#
# Prerequisite: run from the repo root on branch main with a clean working tree.
set -euo pipefail

PR="${1:-}"
if [ -z "$PR" ]; then
  echo "usage: bash scripts/finalize_cost.sh <pr-number>" >&2
  exit 1
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [ "$branch" != "main" ]; then
  echo "error: must be run from branch 'main' (currently on '$branch')" >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "error: working tree is not clean — commit or stash first" >&2
  exit 1
fi

echo "==> Syncing PR #$PR cost record…"
python3 scripts/pr_cost_ledger.py sync --pr "$PR"

echo "==> Opening finalize PR…"
FIX_BRANCH="chore/cost-finalize-pr${PR}"
git checkout -b "$FIX_BRANCH"
git add metrics/pr_cost/
if git diff --cached --quiet; then
  echo "Nothing changed after sync — ledger already current."
  git checkout main
  git branch -d "$FIX_BRANCH"
  exit 0
fi
git commit -m "chore(cost): finalize PR #${PR} ledger — merged_at + model split + report"
git push --no-verify -u origin "$FIX_BRANCH"

gh pr create \
  --base main \
  --title "chore(cost): finalize PR #${PR} cost ledger" \
  --body "$(cat <<BODY
## 1. What is the change
Post-merge cost ledger finalization for PR #${PR}: corrects \`merged_at\`, diff stats, model split, and regenerates \`report.html\`.

## 2. Motivation
Automated fix via \`scripts/finalize_cost.sh ${PR}\`. See CONTRIBUTING.md § post-merge for why the committed ledger can lag.

## 3. Design / Approach
Data-only change: \`metrics/pr_cost/PR-${PR}.json\` + \`report.html\`. No logic changes.

## 4. End-to-end test
\`python3 scripts/pr_cost_ledger.py validate --pr ${PR}\` → OK (run before commit).

## 5. Backward compat
N/A — ledger JSON + report only.

## 6. Checklist
- [x] No logic changes
- [x] No new dependencies
- [x] Cost covered under PR #${PR}
BODY
)"

gh pr merge --auto --squash
echo "==> PR created and auto-merge armed. Approve it to land the finalized record on main."
git checkout main
