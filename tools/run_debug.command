#!/usr/bin/env bash
# Double-clickable (macOS Finder) launcher for debug mode — the .command counterpart of
# run_debug.sh, just as run.command is for run.sh. Kept as a thin wrapper so run_debug.sh
# (and through it run.sh) stays the single source of truth.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./run_debug.sh
