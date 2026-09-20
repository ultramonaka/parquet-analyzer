@echo off
setlocal

set "REPO_ROOT=%~dp0.."
set "PYTHONPATH=%REPO_ROOT%\src;%PYTHONPATH%"

cd /d "%REPO_ROOT%"
uv run python -m parquet_analyzer %*
