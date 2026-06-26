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
# Also removes any legacy Jarvis beforeSubmitPrompt dispatcher from
# ~/.cursor/hooks.json (idempotent; preserves unrelated entries).
#
# Run this once on every fresh clone / laptop.
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

# ---------------------------------------------------------------------------
# Remove legacy Jarvis beforeSubmitPrompt dispatcher from ~/.cursor/hooks.json
# (was added by an earlier version of this script; the blocking hook has been
#  replaced by the /jarvis-new-task Cursor Skill).
# ---------------------------------------------------------------------------
_prune_legacy_dispatcher() {
    local hooks_file="$HOME/.cursor/hooks.json"
    [ -f "$hooks_file" ] || return 0

    python3 - "$hooks_file" <<'PYEOF'
import json, os, sys

hooks_file = sys.argv[1]
# The legacy command string installed by the old dispatcher
legacy_cmd = 'bash "$CURSOR_PROJECT_DIR/.cursor/hooks/enforce.sh"'

try:
    with open(hooks_file) as f:
        doc = json.load(f)
except Exception as e:
    print(f"warning: could not parse {hooks_file}: {e}", file=sys.stderr)
    sys.exit(0)

entries = doc.get("hooks", {}).get("beforeSubmitPrompt", [])
filtered = [e for e in entries if e.get("command") != legacy_cmd]

if len(filtered) == len(entries):
    print("legacy jarvis dispatcher: not present — skipped")
    sys.exit(0)

doc["hooks"]["beforeSubmitPrompt"] = filtered
# Clean up empty lists/dicts
if not doc["hooks"]["beforeSubmitPrompt"]:
    del doc["hooks"]["beforeSubmitPrompt"]
if not doc["hooks"]:
    del doc["hooks"]

with open(hooks_file, "w") as f:
    json.dump(doc, f, indent=2)
    f.write("\n")
print(f"removed legacy jarvis dispatcher from {hooks_file}")
PYEOF
}

_prune_legacy_dispatcher
