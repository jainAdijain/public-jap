#!/usr/bin/env bash
# Prepares a machine to run `make test-host`: the checks CI runs, without the Docker stack.
# For cloud sandboxes (Devin, Codex, Claude Code on the web, Copilot) and any checkout whose
# stack is not running. Safe to run again; each step skips what is already there.
#
#   .venv/                 Python 3.11+ with backend/pyproject.toml [dev], and Chromium for the
#                          apply tests (Playwright)
#   frontend/node_modules  npm ci
#   Postgres + pgvector    the first that works of:
#                            JAP_HOST_DATABASE_URL, when the sandbox already provides one
#                            a `jap-host-db` container on 127.0.0.1:55432, where Docker runs
#                            apt (Debian or Ubuntu, as root or with sudo)
#
# It needs no secret and writes none: no .env, no OpenRouter key, no mailbox. The tests fake
# OpenRouter, Ollama, Inngest and the job sites.
#
# Usage: infra/scripts/agent-setup.sh   (or: make setup-host)
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT=$PWD

say() { printf '\n== %s\n' "$*"; }
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo > /dev/null && sudo -n true 2> /dev/null; then
  SUDO="sudo"
fi
can_root() { [ "$(id -u)" -eq 0 ] || [ -n "$SUDO" ]; }

say "Python: .venv with backend[dev]"
if [ ! -x .venv/bin/python ]; then
  if command -v uv > /dev/null; then
    uv venv --python 3.12 .venv
  else
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
      || { echo "Python 3.11 or newer is needed (or install uv)." >&2; exit 1; }
    python3 -m venv .venv
  fi
fi
# From the lock, so this machine runs the versions CI and the app image run: a ruff or
# mypy that differs by a patch release is a `make test-host` that disagrees with CI for
# reasons nobody can see (IDEA-23). `make lock` repins it.
if command -v uv > /dev/null; then
  (cd backend && uv pip install --quiet --python "$ROOT/.venv/bin/python" \
    --require-hashes -r requirements.lock)
else
  (cd backend && "$ROOT/.venv/bin/python" -m pip install --quiet \
    --require-hashes -r requirements.lock)
fi

say "Chromium for the apply tests"
if [ "$(uname -s)" = "Linux" ] && can_root; then
  $SUDO "$ROOT/.venv/bin/python" -m playwright install --with-deps chromium
else
  .venv/bin/python -m playwright install chromium
fi

say "UI: npm ci"
(cd frontend && npm ci --no-audit --no-fund)

say "Postgres with pgvector"
# Each checkout gets its own database name, so two worktrees sharing one server do not drop
# each other's <name>_test database (tests/conftest.py).
slug=$(basename "$ROOT" | tr -c 'A-Za-z0-9\n' '_' | tr 'A-Z' 'a-z')
if [ -n "${JAP_HOST_DATABASE_URL:-}" ]; then
  url=$JAP_HOST_DATABASE_URL
  echo "Using JAP_HOST_DATABASE_URL from the environment."
elif command -v docker > /dev/null && docker info > /dev/null 2>&1; then
  if [ -z "$(docker ps -aq -f name='^jap-host-db$')" ]; then
    docker run -d --name jap-host-db -p 127.0.0.1:55432:5432 \
      -e POSTGRES_USER=jap -e POSTGRES_PASSWORD=jap -e POSTGRES_DB=jap \
      pgvector/pgvector:pg16 > /dev/null
  else
    docker start jap-host-db > /dev/null
  fi
  printf 'Waiting for jap-host-db '
  for _ in $(seq 1 60); do
    docker exec jap-host-db pg_isready -q -U jap && break
    printf '.'; sleep 1
  done
  echo " up"
  url="postgresql+psycopg://jap:jap@127.0.0.1:55432/jap_$slug"
elif command -v apt-get > /dev/null && can_root; then
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq postgresql > /dev/null
  version=$(ls /usr/lib/postgresql | sort -n | tail -1)
  $SUDO apt-get install -y -qq "postgresql-$version-pgvector" > /dev/null
  $SUDO service postgresql start
  $SUDO su postgres -c "psql -qc \"DO \\\$\\\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jap') THEN CREATE ROLE jap LOGIN SUPERUSER PASSWORD 'jap'; END IF; END \\\$\\\$\""
  url="postgresql+psycopg://jap:jap@127.0.0.1:5432/jap_$slug"
else
  echo "No Postgres: Docker is not running and apt is not usable here." >&2
  echo "Start one with pgvector and run again with JAP_HOST_DATABASE_URL set." >&2
  exit 1
fi
# make test-host reads the address from here (a local jap:jap login, not a secret).
printf '%s\n' "$url" > .venv/jap-host-db-url

say "Ready. Run: make test-host"
