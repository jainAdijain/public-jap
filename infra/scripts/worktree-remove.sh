#!/usr/bin/env bash
# Removes a worktree made by worktree.sh: its stack and volumes, the checkout, and the
# worktree registration. The branch is kept, so merged or not, the commits are still there.
#
# This deletes that worktree's database and its data/ folder, so it asks first.
#
# Usage: worktree-remove.sh <name>        (make worktree-remove NAME=<name>)
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD

NAME=${1:-}
case "$NAME" in
  "" | *[!a-z0-9-]*) echo "Usage: make worktree-remove NAME=<name>" >&2; exit 1 ;;
esac
DIR="${JAP_WORKTREE_DIR:-$(dirname "$ROOT")}/$(basename "$ROOT")-$NAME"
[ -d "$DIR" ] || { echo "No worktree at $DIR." >&2; exit 1; }

printf 'This removes %s: its database, its data/ folder and the checkout.\n' "$DIR"
printf 'The branch is kept. Anything uncommitted there is lost. Type yes to continue: '
read -r answer
[ "$answer" = "yes" ] || { echo "Cancelled."; exit 1; }

docker compose -p "jap-$NAME" --env-file "$DIR/.env" -f infra/compose.yaml down -v --remove-orphans || true
git worktree remove --force "$DIR"
echo "Removed. The branch is still there: git branch --list '$NAME'"
