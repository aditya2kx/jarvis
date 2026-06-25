#!/usr/bin/env bash
# Cursor beforeSubmitPrompt hook — new-requirement intake gate.
#
# Thin wrapper: delegates all logic to prompt_gate.py which lives alongside
# this script in the repo-versioned .cursor/hooks/ directory.
#
# This script is invoked by the one-time per-laptop user-level dispatcher in
# ~/.cursor/hooks.json (installed by scripts/install-git-hooks.sh).
# The enforcement logic travels with the repo/branch; the dispatcher is stable.
#
# Bypass: prefix your prompt with  //inline  to skip the intake check.
# See .cursor/hooks/prompt_gate.py for detection logic and BLOCK_MESSAGE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/prompt_gate.py"
