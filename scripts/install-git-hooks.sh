#!/usr/bin/env bash
# Install the repo's git hooks (pre-commit + post-merge cost-ledger sync).
# Points git at scripts/git-hooks via core.hooksPath — undo with:
#   git config --unset core.hooksPath
#
# pre-commit: folds the per-PR cost ledger (metrics/pr_cost/) into your own
#   commits so it lands on main in the squash merge — no bot commit, no CI
#   push-back. Run this once on every fresh clone / worktree (including cloud
#   agents). See CONTRIBUTING.md § Cost ledger.
#
# post-merge: after `git pull` on main, regenerates report.html and backfills
#   merged_at for all merged PRs so the local report is correct immediately —
#   fixing the "report is one PR behind" gap (see CONTRIBUTING.md § post-merge).
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
chmod +x scripts/git-hooks/* 2>/dev/null || true
git config core.hooksPath scripts/git-hooks
echo "installed: core.hooksPath=scripts/git-hooks"
echo "  pre-commit : capture-review + report (ledger rides in your commits)"
echo "  post-merge : report regeneration on main (merged_at backfill, local only)"
echo "uninstall: git config --unset core.hooksPath"
