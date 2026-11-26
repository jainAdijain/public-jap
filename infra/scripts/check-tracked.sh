#!/usr/bin/env sh
# Fails if git holds, or is about to hold, something this public repo must never carry:
# a secrets file, a private product document (docs/intent/sources/, docs/private/, docs/text/, a PDF, or anything in
# docs/ that is not a Markdown page: the PRD and design doc carry personal data), runtime data,
# local notes, or a line that looks like an OpenRouter key. docs/*.md is the public project
# documentation and is tracked on purpose.
#
# .gitignore already keeps these out; this catches `git add -f` and a key pasted into a
# tracked file, whichever agent or person did it.
#
# Usage: check-tracked.sh           every tracked file (CI, make test-agents)
#        check-tracked.sh --staged  what the next commit adds (.githooks/pre-commit)
set -eu
cd "$(git rev-parse --show-toplevel)"
STAGED=${1:-}

list_files() {
  if [ "$STAGED" = "--staged" ]; then
    git diff --cached --name-only --diff-filter=ACMR
  else
    git ls-files
  fi
}

# One name per line throughout: a file name may contain spaces. The case lives in a function
# because bash 3.2, which is /bin/sh on macOS, cannot parse one inside $( ).
classify() {
  while IFS= read -r f; do
    case "$f" in
      .env.example | data/.gitkeep) ;;
      .env | .env.* | */.env | */.env.*) echo "secrets file: $f" ;;
      docs/intent/sources/* | docs/private/* | docs/text/*) echo "product document (personal data): $f" ;;
      docs/*.md | docs/*/*.md) ;;
      docs/*) echo "not a Markdown page, so possibly a product document: $f" ;;
      data/* | data-live/*) echo "runtime data: $f" ;;
      *.local.md | *.local.json) echo "local-only file: $f" ;;
    esac
  done
}

bad=$(list_files | classify)

# Name the file, never the line: the line is the key.
if [ "$STAGED" = "--staged" ]; then
  keyed=$(git grep --cached -lE 'sk-or-v1-[A-Za-z0-9]{32,}' || true)
else
  keyed=$(git grep -lE 'sk-or-v1-[A-Za-z0-9]{32,}' || true)
fi
keyed=$(printf '%s' "$keyed" | sed '/^$/d; s/^/looks like an OpenRouter key: /')

found=$(printf '%s\n%s' "$bad" "$keyed" | sed '/^$/d')
if [ -n "$found" ]; then
  printf '%s\n' "$found"
  echo "Refused: this repo is public. Unstage the files above (git restore --staged <file>)." >&2
  exit 1
fi
