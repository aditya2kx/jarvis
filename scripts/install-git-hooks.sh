#!/usr/bin/env bash
# Install the repo's git hooks.  Points git at scripts/git-hooks via
# core.hooksPath — undo with: git config --unset core.hooksPath
#
# Hooks installed:
#   pre-commit : captures review cost from PR comments into BigQuery (jarvis_dev)
#                on each commit.  Build cost must be recorded explicitly by the
#                author (record-build or capture-build).  The gate validates from BQ.
#                Set PR_COST_HOOK=0 to skip a WIP commit.
#   pre-push   : runs scripts/verify.py --full — the full local CI mirror — before
#                any push.  Catches secret-scan, test, doc-freshness, PR-desc, and
#                review-replies failures before they hit CI.
#                Set VERIFY=0 to bypass (only after confirming the diff is clean).
#
# Run this once on every fresh clone / worktree.
# See docs/contributing/local-loop.md and docs/contributing/cost.md.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
chmod +x scripts/git-hooks/* 2>/dev/null || true
git config core.hooksPath scripts/git-hooks

# Seed GitHub Issue labels for phase tracking (idempotent; requires gh auth).
if command -v gh >/dev/null 2>&1 && python3 -c "import scripts.phase_state" 2>/dev/null; then
    python3 scripts/phase_state.py ensure-labels 2>/dev/null || true
fi

echo "installed: core.hooksPath=scripts/git-hooks"
echo "  pre-commit : capture-review -> BQ  (bypass: PR_COST_HOOK=0)"
echo "  pre-push   : verify.py --full      (bypass: VERIFY=0)"
echo "uninstall: git config --unset core.hooksPath"
