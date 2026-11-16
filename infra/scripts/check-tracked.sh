#!/usr/bin/env sh
# Fails if git holds, or is about to hold, something this public repo must never carry:
# a secrets file, anything from docs/ (the PRD and design doc carry personal data), runtime
# data, local notes, or a line that looks like an OpenRouter key.
#
# .gitignore already keeps these out; this catches `git add -f` and a key pasted into a
# tracked file, whichever agent or person did it.
#
# Usage: check-tracked.sh           every tracked file (CI, make test-agents)
#        check-tracked.sh --staged  what the next commit adds (.githooks/pre-commit)
set -eu
cd "$(git rev-parse --show-toplevel)"

if [ "${1:-}" = "--staged" ]; then
  files=$(git diff --cached --name-only --diff-filter=ACMR)
else
  files=$(git ls-files)
fi

bad=0
for f in $files; do
  case "$f" in
    .env.example|infra/tunnel/.env.tunnel.example|data/.gitkeep) ;;
    .env|.env.*|*/.env|*/.env.*) echo "secrets file: $f"; bad=1 ;;
    docs/*) echo "product document (personal data): $f"; bad=1 ;;
    data/*|data-live/*) echo "runtime data: $f"; bad=1 ;;
    NOTES.local.md|*.local.md|*.local.json) echo "local-only file: $f"; bad=1 ;;
  esac
done

# Name the file, never the line: the line is the key.
if [ -n "$files" ]; then
  if [ "${1:-}" = "--staged" ]; then
    keyed=$(git grep --cached -lE 'sk-or-v1-[A-Za-z0-9]{32,}' -- $files || true)
  else
    keyed=$(git grep -lE 'sk-or-v1-[A-Za-z0-9]{32,}' -- $files || true)
  fi
  for f in $keyed; do echo "looks like an OpenRouter key: $f"; bad=1; done
fi

if [ "$bad" -ne 0 ]; then
  echo "Refused: this repo is public. Unstage the files above (git restore --staged <file>)." >&2
  exit 1
fi
