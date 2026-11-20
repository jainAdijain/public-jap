# JAP: one command per job. Run `make help` for the list.
#
# Every target takes ENV=local|live (PRD H7); local is the default, so `make start` works as
# before. The two share nothing: their own env file, Docker project, database volume, data
# folder and ports.
#
#   local  .env       this working copy with live reload, never submits, not on the internet
#   live   .env.live  the server at jap.tutorial.monster: submits, sends email, holds other
#                     people's data
#
# local runs on your Mac. **live runs on the server and nowhere else** (README, Hosting):
# it builds nothing, it runs the images GitHub Actions pushed for a version tag, and those
# are linux/amd64, so a laptop cannot start it even by accident. On the server a release is
# `make deploy TAG=vX.Y.Z`, which checks out the tag and pulls the images it names.
ENV ?= local
ifeq ($(ENV),local)
ENV_FILE := .env
else ifeq ($(ENV),live)
ENV_FILE := .env.live
else
$(error ENV must be local or live)
endif

# Ports, project name, data folder and JAP_RELEASE: the environment's file wins, these are
# the defaults (keep in step with infra/compose.yaml).
-include $(ENV_FILE)
JAP_PROJECT ?= jap
JAP_UI_PORT ?= 5173
JAP_API_PORT ?= 8001
JAP_INNGEST_PORT ?= 8290
JAP_ADMINER_PORT ?= 8080
JAP_DATA_HOST_DIR ?= data
HEALTH_URL = http://127.0.0.1:$(JAP_API_PORT)/api/health

# Is this the release stack (live)? Used to choose pull over build, and to refuse the things
# that only make sense on the server.
RELEASE := $(filter 1 true,$(JAP_RELEASE))

# The images live runs are tagged with the git tag they were built from, so the tag that is
# checked out decides which image starts and the two can never drift apart. Exported, because
# compose reads it when it renders infra/compose.release.yaml; empty off a tag, which is what
# makes `make start ENV=live` stop and ask for `make deploy TAG=...` instead.
export JAP_IMAGE_TAG ?= $(shell git describe --tags --exact-match 2>/dev/null)

COMPOSE_FILES := -f infra/compose.yaml $(if $(RELEASE),-f infra/compose.release.yaml)
COMPOSE := docker compose -p $(JAP_PROJECT) --env-file $(ENV_FILE) $(COMPOSE_FILES)

.DEFAULT_GOAL := help
.PHONY: help env deploy tunnel-status start stop restart reset clean logs ps migrate seed test test-app test-ui test-agents test-host setup-host hooks worktree worktree-list worktree-remove qa backup backup-pull restore close-unfollowed form-check health-check errors two-step-off sign-out-all set-password email-test code-mail-check ollama-check llm-check demo demo-clear

help: ## List the targets
	@grep -hE '^[a-z-]+:.*## ' $(firstword $(MAKEFILE_LIST)) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-13s %s\n", $$1, $$2}'

env: ## Create this environment's file if it is missing (.env, or .env.live with fresh keys)
ifeq ($(ENV),local)
	@test -f .env || { cp .env.example .env; echo "Created .env from .env.example. Add your OpenRouter key and email password to it."; }
else
	@test -f .env.live || infra/scripts/env-create.sh
endif

start: env ## Start the stack, wait for /api/health, apply migrations
ifneq ($(RELEASE),)
	@test "$$(uname)" != Darwin || { echo "live runs on the server, not this Mac (README, Hosting). Its images are linux/amd64 and it holds other people's data."; exit 1; }
	@test -n "$(JAP_IMAGE_TAG)" || { echo "No version tag is checked out, so there is no image to run. Release with: make deploy ENV=live TAG=vX.Y.Z"; exit 1; }
	$(COMPOSE) pull
	$(COMPOSE) up -d
else
	$(COMPOSE) up -d --build
endif
	@infra/scripts/wait-for-health.sh $(HEALTH_URL)
	@$(MAKE) --no-print-directory migrate
	@$(MAKE) --no-print-directory ollama-check
	@echo "$(ENV): UI http://localhost:$(JAP_UI_PORT)  API http://localhost:$(JAP_API_PORT)  Inngest http://localhost:$(JAP_INNGEST_PORT)$(if $(RELEASE),,  Adminer http://localhost:$(JAP_ADMINER_PORT))"

stop: ## Stop the stack (data is kept)
	$(COMPOSE) down

restart: stop start ## Stop, then start

reset: env ## Wipe the database and start fresh: down -v, up, migrate, seed (asks first)
	@printf 'This deletes the JAP database (profile, jobs, applications). data/ is kept. Type yes to continue: '; \
	read answer; [ "$$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
	$(COMPOSE) down -v
	$(COMPOSE) up -d --build
	@infra/scripts/wait-for-health.sh $(HEALTH_URL)
	@$(MAKE) --no-print-directory migrate seed

clean: ## Remove containers, volumes, JAP images and build caches (asks first; data/ is kept)
	@printf 'This removes JAP containers, volumes, images and build caches. data/ is kept. Type yes to continue: '; \
	read answer; [ "$$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
	-$(COMPOSE) down -v --rmi local --remove-orphans
	-docker builder prune -f
	rm -rf backend/.mypy_cache backend/.ruff_cache backend/.pytest_cache frontend/node_modules frontend/dist
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +

logs: ## Follow all service logs
	$(COMPOSE) logs -f

ps: ## Show the running services
	$(COMPOSE) ps

migrate: ## Apply database migrations (alembic upgrade head)
	$(COMPOSE) exec app alembic upgrade head

seed: ## Load seed/companies.yaml and seed/skills.yaml (safe to repeat)
	$(COMPOSE) exec app python -m database.seed

demo: ## Load made-up postings for checking dedup and filters by hand (never fetched)
	$(COMPOSE) exec app python -m database.demo load

demo-clear: ## Remove the demo postings, listings and boards
	$(COMPOSE) exec app python -m database.demo clear

test: test-app test-ui test-agents ## ruff, mypy and pytest for app; lint and build for ui; agent guards

# The tests run against known settings, not whatever this stack happens to use:
#   JAP_ROLE=all     the app container is the API half and does not mount /api/inngest; the
#                    suite checks the whole app, and tests/test_hosting.py builds one of
#                    each half to check the split itself
#   the UI address   a worktree's stack is on a port of its own (5503, 5603, ...), and the
#                    tests assert on the default, as a reset link and an allowed origin
#                    should not depend on which port a developer was given
#   submitting on    the apply tests fill and submit forms that are local fakes, and the
#                    switch is what they are testing; a worktree's stack has it off so the
#                    stack can never submit, and that must not disable the tests of it.
#                    Only this process sees it: the running app container keeps its own.
TEST_ENV := -e JAP_ROLE=all -e JAP_UI_URL=http://localhost:5173 \
	-e CORS_ORIGINS='["http://localhost:5173"]' -e JAP_SUBMIT_ENABLED=true

test-app:
	$(COMPOSE) exec $(TEST_ENV) app sh -c 'ruff check . && ruff format --check . && mypy . && pytest -q'

test-ui:
	$(COMPOSE) exec ui sh -c 'npm run lint && npm run build'

setup-host: ## Prepare this machine for test-host: .venv, Chromium, npm ci, a Postgres (no Docker stack)
	@infra/scripts/agent-setup.sh

# The address setup-host found or started; a sandbox that brings its own Postgres sets it.
JAP_HOST_DATABASE_URL ?= $(shell cat .venv/jap-host-db-url 2>/dev/null)

test-host: ## The checks of `make test` on this machine, without the stack (cloud sandboxes; what CI runs)
	@test -x .venv/bin/pytest -a -d frontend/node_modules -a -n "$(JAP_HOST_DATABASE_URL)" || { echo "Not set up for host tests. Run: make setup-host"; exit 1; }
	cd backend && PATH="$(CURDIR)/.venv/bin:$$PATH" DATABASE_URL="$(JAP_HOST_DATABASE_URL)" JAP_REQUIRE_DB=1 OPENROUTER_API_KEY= \
		sh -c 'ruff check . && ruff format --check . && mypy . && pytest -q'
	cd frontend && npm run lint && npm run build
	@$(MAKE) --no-print-directory test-agents

test-agents: ## The coding-agent guards: hook self-test, and nothing private is tracked
	@python3 infra/scripts/agent-guard.py --self-test
	@infra/scripts/check-tracked.sh && echo "check-tracked: nothing private is tracked"

hooks: ## Turn on the pre-commit check for this clone (git config core.hooksPath .githooks)
	git config core.hooksPath .githooks

worktree: ## A sibling worktree with its own stack, for an agent on a branch: make worktree NAME=<name> [BRANCH=<branch>]
	@infra/scripts/worktree.sh "$(NAME)" $(BRANCH)

worktree-list: ## The worktrees and the ports each one's stack uses
	@git worktree list
	@grep -s -H '^JAP_UI_PORT=' ../$(notdir $(CURDIR))-*/.env 2>/dev/null || true

worktree-remove: ## Remove a worktree, its stack and its data; the branch is kept (asks first)
	@infra/scripts/worktree-remove.sh "$(NAME)"

qa: ## Dedup QA set: place ~100 labelled job pairs and print the accuracy
	$(COMPOSE) exec app pytest -q -s tests/test_dedup_qa.py

backup: ## Dump the database to this environment's backups folder
	@DATA_DIR="$(JAP_DATA_HOST_DIR)" COMPOSE="$(COMPOSE)" infra/scripts/backup.sh

backup-pull: ## Copy the server's newest dump to this Mac: make backup-pull SERVER=jap
	@infra/scripts/backup-pull.sh "$(SERVER)"

restore: ## Restore a backup: make restore FILE=data/backups/<file>.sql.gz
	@DATA_DIR="$(JAP_DATA_HOST_DIR)" COMPOSE="$(COMPOSE)" infra/scripts/restore.sh "$(FILE)"

close-unfollowed: ## List open postings of unfollowed companies; APPLY=1 closes them (back up first)
	$(COMPOSE) exec app python -m pipeline.unfollow $(if $(filter 1 true,$(APPLY)),--apply)

health-check: ## Run the hourly health watch now: delete old log rows, email alerts that are due
	@curl -fsS -X POST http://127.0.0.1:$(JAP_API_PORT)/api/ops/health-check > /dev/null && \
		echo "Health check started. Results: Settings > Status, or the health-watch run in Inngest."

errors: ## The last 20 warnings and errors JAP logged (log_event, kept 30 days)
	@$(COMPOSE) exec -T db psql -U $(or $(POSTGRES_USER),jap) -d $(or $(POSTGRES_DB),jap) -c \
		"SELECT to_char(created_at, 'MM-DD HH24:MI') AS at, level, event, left(message, 90) AS message FROM log_event WHERE level IN ('warning', 'error', 'critical') ORDER BY created_at DESC LIMIT 20"

form-check: ## Read one live form per ATS now (the weekly form test; nothing is submitted)
	@$(COMPOSE) exec -T app python -m functions.events form-check/requested > /dev/null && \
		echo "Form test started. Results: System status on Settings, or the form-check run in Inngest."

two-step-off: ## Turn two-step sign-in off for an account: make two-step-off EMAIL=<address>
	@test -n "$(EMAIL)" || { echo "Usage: make two-step-off EMAIL=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m auth.cli two-step-off "$(EMAIL)"

sign-out-all: ## End every session of an account: make sign-out-all EMAIL=<address>
	@test -n "$(EMAIL)" || { echo "Usage: make sign-out-all EMAIL=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m auth.cli sign-out-all "$(EMAIL)"

set-password: ## Set a new password without email: make set-password EMAIL=<address>
	@test -n "$(EMAIL)" || { echo "Usage: make set-password EMAIL=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m auth.cli set-password "$(EMAIL)"

email-test: ## Send one test email from the mailbox in .env: make email-test TO=<address>
	@test -n "$(TO)" || { echo "Usage: make email-test TO=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m pipeline.notify test "$(TO)"

code-mail-check: ## Log in to the mailbox over IMAP and count Greenhouse emails (security codes)
	$(COMPOSE) exec app python -m pipeline.apply.security_code

llm-check: ## One test call to each OpenRouter model (needs OPENROUTER_API_KEY in .env)
	$(COMPOSE) exec app python -m llm.check

ollama-check: ## Check that Ollama answers with the embedder pulled, at the address the app uses
	@$(COMPOSE) exec -T app python -m llm.ollama || true

# ---- Hosting (PRD H1 to H8; README, Hosting) ----
#
# live is the server. The tunnel is part of its stack now, so `make start ENV=live` puts the
# site on the internet and `make stop ENV=live` takes it off; there is no separate tunnel to
# start. A release is `make deploy`.

deploy: ## On the server: check out a release tag and start it. make deploy ENV=live TAG=vX.Y.Z
	@infra/scripts/deploy.sh "$(ENV)" "$(TAG)"

tunnel-status: ## Is the Cloudflare Tunnel connected? (last log lines)
	@$(COMPOSE) ps cloudflared
	@$(COMPOSE) logs --tail 5 cloudflared 2>/dev/null || true
