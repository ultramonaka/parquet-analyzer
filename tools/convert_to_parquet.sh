#!/usr/bin/env bash
set -euo pipefail

# Convert an MDF or MATLAB measurement file to Parquet. See core/convert.py for the
# conversion logic and convert_cli.py for the argument parsing.
#
# Usage: tools/convert_to_parquet.sh <input.mf4|.mdf|.dat|.mat> [output.parquet] [options]

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

cd "$REPO_ROOT"
uv run --group convert python -m parquet_analyzer.convert_cli "$@"
