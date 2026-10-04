#!/usr/bin/env bash
# Readable timeline of an operation log: tools/show_ops_log.sh [FILE] [--last N] [--session]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

cd "$REPO_ROOT"
exec uv run python -m parquet_analyzer.core.oplog_view "$@"
