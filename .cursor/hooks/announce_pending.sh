#!/usr/bin/env bash
# announce_pending.sh — sessionStart hook
# Surfaces pending events as context when a chat opens in this worktree.
# Returns {} (no output modification needed — context is informational only).
set -euo pipefail

command -v git >/dev/null 2>&1 || { echo '{}'; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo '{}'; exit 0; }

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
  echo '{}'
  exit 0
fi

SLUG="$(python3 -c "import re; print(re.sub(r'[^a-zA-Z0-9_-]','-','$BRANCH')[:60])" 2>/dev/null || true)"
if [ -z "$SLUG" ]; then
  echo '{}'
  exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PHASE_FILE="$REPO_ROOT/metrics/pr_cost/session-${SLUG}-phase.json"
INBOX_FILE="$REPO_ROOT/metrics/pr_cost/session-${SLUG}-pending.jsonl"

if [ ! -f "$INBOX_FILE" ]; then
  echo '{}'
  exit 0
fi

COUNT="$(wc -l < "$INBOX_FILE" | tr -d ' ')"
if [ "$COUNT" -eq 0 ]; then
  echo '{}'
  exit 0
fi

# Peek at the first event kind
FIRST_KIND="$(head -1 "$INBOX_FILE" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('kind','?'))" 2>/dev/null || echo "?")"

# Print an announcement to stdout for the agent to see as session context
echo "=== Jarvis: $COUNT pending event(s) in inbox (next: $KIND) ===" >&2
echo "Run: python3 scripts/dev_event_router.py drain --branch $BRANCH" >&2

echo '{}'
exit 0
