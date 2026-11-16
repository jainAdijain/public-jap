#!/usr/bin/env bash
# A git worktree that can run its own JAP stack, for an agent working on a branch while the
# main checkout keeps running (AGENTS.md, Several agents at once).
#
# The worktree is a sibling of this checkout (../<repo>-<name>) on branch <name>, with its
# own .env: its own Docker project, ports, database volume and data/ folder, so `make start`
# or `make reset` there cannot touch the stack you are using. It copies this checkout's keys
# (OpenRouter, mailbox) by writing them, never printing them, and forces
# JAP_SUBMIT_ENABLED=false and JAP_ALERTS_ENABLED=false: a worktree never submits an
# application and never sends an alert email.
#
# Usage: worktree.sh <name> [branch]      (make worktree NAME=<name> [BRANCH=<branch>])
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD

NAME=${1:-}
BRANCH=${2:-$NAME}
case "$NAME" in
  "" | *[!a-z0-9-]*) echo "Usage: make worktree NAME=<lower-case-name> [BRANCH=<branch>]" >&2; exit 1 ;;
esac

BASE=${JAP_WORKTREE_DIR:-$(dirname "$ROOT")}
DIR="$BASE/$(basename "$ROOT")-$NAME"
[ -e "$DIR" ] && { echo "$DIR already exists." >&2; exit 1; }

# Ports: slot n gives 55n1/85n1/86n1/57n1/87n1, a range neither local nor live uses. The slot
# is the lowest not already claimed by a sibling worktree's .env.
slot=0
for n in $(seq 1 99); do
  if ! grep -qs "^JAP_UI_PORT=$((5500 + n))$" "$BASE"/*/.env; then slot=$n; break; fi
done
[ "$slot" -eq 0 ] && { echo "No free port slot (99 worktrees?)." >&2; exit 1; }
ui=$((5500 + slot)) api=$((8500 + slot)) inngest=$((8600 + slot))
db=$((5700 + slot)) adminer=$((8700 + slot))

if git show-ref --quiet "refs/heads/$BRANCH"; then
  git worktree add "$DIR" "$BRANCH"
else
  git worktree add -b "$BRANCH" "$DIR"
fi

# The source of the copy: this checkout's .env when it has one, else the example. Both are
# read by awk and written straight to the new file; no value is ever printed.
SOURCE="$ROOT/.env"
[ -f "$SOURCE" ] || SOURCE="$ROOT/.env.example"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" << OVERRIDES
JAP_ENV=dev
JAP_ENV_FILE=.env
JAP_PROJECT=jap-$NAME
JAP_RELEASE=0
JAP_SUBMIT_ENABLED=false
JAP_ALERTS_ENABLED=false
JAP_DATA_HOST_DIR=data
JAP_UI_URL=http://localhost:$ui
JAP_UI_PORT=$ui
JAP_API_PORT=$api
JAP_INNGEST_PORT=$inngest
JAP_DB_PORT=$db
JAP_ADMINER_PORT=$adminer
OVERRIDES
awk -F= 'NR == FNR { v[$1] = $0; next }
  /^[A-Z_][A-Z0-9_]*=/ && ($1 in v) { print v[$1]; done[$1] = 1; next }
  { print }
  END { for (k in v) if (!(k in done)) print v[k] }' "$TMP" "$SOURCE" > "$DIR/.env"
chmod 600 "$DIR/.env"

printf '\nWorktree %s on branch %s\n' "$DIR" "$BRANCH"
printf '  keys copied from %s (never printed); submitting and alert email are off\n' \
  "$(basename "$SOURCE")"
printf '  docker project jap-%s, ports ui %s, api %s, inngest %s, db %s, adminer %s\n' \
  "$NAME" "$ui" "$api" "$inngest" "$db" "$adminer"
printf '\nIn it:  make start      the stack, apart from every other one\n'
printf '        make test       once that stack is up\n'
printf '        make setup-host && make test-host    to check without starting a stack\n'
printf '\nWhen the branch is merged:  make worktree-remove NAME=%s\n' "$NAME"
