#!/usr/bin/env bash
set -euo pipefail

# Mirrors this repo's git-TRACKED files into a separate public-repo directory, then
# commits the result there. Intended to be run repeatedly as dev work continues here
# (see LEARNINGS.md 2026-09-17 "公開リポジトリへの反映"): each run re-syncs the public
# repo to the current tracked-file state and makes one new commit on top of whatever
# history the public repo already has. It does NOT import this repo's own git history
# and it never pushes — review the commit and push yourself.
#
# "Tracked files" = `git ls-files` in this repo, i.e. exactly what .gitignore already
# keeps out of version control (data/, cfg/, log/, .venv/, __pycache__/, etc.) is
# excluded automatically. On top of that, PUBLIC_EXCLUDES below removes a small,
# explicit list of tracked files that are still not meant for the public copy --
# internal dev-workflow/agent guidance, not something a public repo's readers need:
# CLAUDE.md (Claude Code agent instructions), CONVENTIONS.md (repo-structure
# rationale written for whoever/whatever sets up a similar project, not this
# project's own end users), and LEARNINGS.md (internal design-decision history).
#
# Usage:
#   tools/publish_to_public.sh <path-to-public-repo> ["commit message"]
#
# <path-to-public-repo> is created (and `git init`-ed) on first use if it doesn't
# exist yet; on later runs it's just re-synced and committed.

PUBLIC_EXCLUDES=(
  "CLAUDE.md"
  "CONVENTIONS.md"
  "LEARNINGS.md"
)

if [ $# -lt 1 ]; then
  echo "usage: $0 <path-to-public-repo> [\"commit message\"]" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_ARG="$1"
MESSAGE="${2:-Sync from dev repo ($(date +%Y-%m-%d))}"

mkdir -p "$DEST_ARG"
DEST="$(cd "$DEST_ARG" && pwd)"

# Refuse to run if DEST is (or is inside) the dev repo — the wipe step below would
# otherwise delete this repo's own working tree.
case "$DEST" in
  "$REPO_ROOT" | "$REPO_ROOT"/*)
    echo "error: destination ($DEST) is inside the dev repo ($REPO_ROOT) — refusing to run" >&2
    exit 1
    ;;
esac

if [ ! -d "$DEST/.git" ]; then
  echo "==> $DEST has no .git yet; initializing a fresh repo (no history imported from dev repo)"
  git init -q "$DEST"
fi

echo "==> Wiping $DEST (except .git) and re-populating from '$REPO_ROOT' git-tracked files"
find "$DEST" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
git -C "$REPO_ROOT" ls-files -z | tar -C "$REPO_ROOT" --null -T - -cf - | tar -C "$DEST" -xf -

if [ ${#PUBLIC_EXCLUDES[@]} -gt 0 ]; then
  echo "==> Removing files not meant for the public repo: ${PUBLIC_EXCLUDES[*]}"
  for f in "${PUBLIC_EXCLUDES[@]}"; do
    rm -f "$DEST/$f"
  done
fi

cd "$DEST"
git add -A
if git diff --cached --quiet; then
  echo "==> Nothing changed since the last sync; nothing to commit."
  exit 0
fi

git commit -q -m "$MESSAGE"
echo
echo "==> Committed locally in $DEST:"
git log -1 --stat
echo
echo "==> Not pushed. Review, then push yourself, e.g.:"
echo "    git -C \"$DEST\" push"
