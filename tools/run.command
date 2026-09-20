#!/usr/bin/env bash
# macOS Finder runs .command files (unlike .sh) directly when double-clicked, opening
# a Terminal window automatically. Kept as a thin wrapper so run.sh stays the single
# source of truth instead of duplicating its logic.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./run.sh
