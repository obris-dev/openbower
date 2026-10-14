# COMPOSE_PROJECT_NAME in the environment OVERRIDES the compose file's
# own `name:`, which would split one checkout into two projects with
# their own containers and volumes. Passing -p makes the pin
# authoritative over the environment.
PROJECT := openbower
COMPOSE := docker compose -p $(PROJECT)
# Exported as well as passed, so a raw `docker compose` in any recipe or
# sub-shell resolves to the same project.
export COMPOSE_PROJECT_NAME := $(PROJECT)

# The operator command owns the lifecycle (seeding the config files,
# the suite network, up/stop/upgrade, the model setup); the targets
# below delegate to it so that logic lives once, and a self-hoster
# never needs make. Dev-only targets (the restarts, the prunes, the
# tests, the schema) stay here.
ADMIN := sh scripts/bower-admin.sh

# A private deployments checkout beside this one (../deployments) carries
# the hosted-operations targets (release, deploy, migrate, the cluster
# logs). Its make fragments resolve their paths through DEPLOY_DIR, so
# they work from here, and the leading `-` skips the include silently
# when no such checkout exists: a clone of this repo alone is complete.
DEPLOY_DIR ?= ../deployments
-include $(DEPLOY_DIR)/make/*.mk

.DEFAULT_GOAL := help
.PHONY: help hooks install-admin doctor setup providers upgrade db-up up build down reset stop restart restart-core restart-worker restart-cron restart-web reset-web-deps prune-venvs logs logs-core logs-worker logs-ingest logs-autofill logs-cron logs-jobs logs-web prune-preview-runs prune-webhook-deliveries flush-deferred run-jobs receiver receiver-stop local-exec local-manage local-dbshell test-core test-web test schema schema-check

help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

hooks: ## Install the git pre-commit hook (branch-name check)
	@hooks_dir="$$(git rev-parse --git-path hooks)"; \
	hook="$$hooks_dir/pre-commit"; \
	if [ -e "$$hook" ] && ! grep -q check-branch-name "$$hook" 2>/dev/null; then \
		echo "refusing to overwrite an existing $$hook (not ours)"; exit 1; \
	fi; \
	mkdir -p "$$hooks_dir"; \
	printf '#!/bin/sh\nexec scripts/check-branch-name.sh\n' > "$$hook"; \
	chmod +x "$$hook"; \
	echo "pre-commit hook installed at $$hook"

install-admin: ## Put `bower-admin` on your PATH for this checkout (the installer does this for a self-hoster)
	@$(ADMIN) shim

doctor: ## The prerequisites checklist (OS, git, Docker + Compose, the daemon)
	@$(ADMIN) doctor

setup: ## Probe the inference sources in config/providers.toml and add one when none answers
	@$(ADMIN) setup

providers: ## The source matrix: what each inference source serves, or why not (needs the api running)
	@$(ADMIN) providers

upgrade: ## Move this checkout to the newest release and rebuild; data preserved
	@$(ADMIN) upgrade

db-up: ## Start just the database (what host-run tests need)
	@$(ADMIN) up db

# Seeds the config files when absent (never overwrites), creates the
# suite network, and runs `setup` once, until config/providers.toml
# exists.
up: ## Start the full local stack in Docker, detached (api :8002, app :3003, marketing :3004, fill services + jobs + cron)
	@$(ADMIN) up

# Do not interrupt: a Ctrl-C while the worker is being recreated leaves it
# REMOVED with no policy to bring it back, and nothing else drains the
# fill queue. Recover with `make up`.
build: ## Rebuild after Dockerfile/dependency changes (waits for rows in flight, which can take minutes)
	@$(ADMIN) build

# The receiver (make receiver) sits on the stack's network as a plain
# container, and compose cannot remove a network with a foreign endpoint
# still attached, so it goes first; its stop is idempotent.
down: ## Stop and remove the stack's containers (the db's data and installed dependencies survive)
	@./scripts/debug/webhook-receiver.sh stop >/dev/null 2>&1 || true
	@$(ADMIN) down

reset: ## Remove the stack AND its volumes (wipes the dev database and the installed dependencies)
	@./scripts/debug/webhook-receiver.sh stop >/dev/null 2>&1 || true
	$(COMPOSE) down -v

stop: ## Stop the stack in place; the fill consumer finishes the row in flight before its grace expires (make up resumes)
	@$(ADMIN) stop

# Compose-file edits are applied by RECREATING a service, which restart
# does not do: docker compose up -d --no-deps <service>. Without
# --no-deps that also re-runs core-setup, whose cold venv sync is what
# core's start period is sized for.
restart: ## Restart all services in place (compose-file edits need make up, which recreates)
	@$(ADMIN) restart

restart-core: ## Restart just the api
	$(COMPOSE) restart core

restart-worker: ## Restart the fill + autofill provisioners and consumers and the jobs service (what a change to their shared code needs; no reloader)
	$(COMPOSE) restart fill-provisioner fill-consumer autofill-provisioner autofill-consumer jobs

restart-cron: ## Restart the cron (a crontab schedule edit needs it; supercronic parses at startup)
	$(COMPOSE) restart cron

restart-web: ## Restart just the web container (app + marketing); use when host-side edits have confused its dev server
	$(COMPOSE) restart web

# The reclaim path for the anonymous .venv volumes that build and down
# cycles strand, one per container. Scoped by NAME SHAPE only (a 64-hex
# id), so it can never match a named volume (this stack names every one
# that matters), but it IS daemon-wide: another project's dangling
# anonymous volumes go with it, including a stack whose database volume
# was never named.
# The web dependency volumes are NAMED, so they survive `down` and a
# rebuild by design. A pnpm MAJOR bump is the case where that is wrong:
# the per-package trees keep the old major's layout while the root is
# rewritten, and modules stop resolving. Recreating them is the migration.
reset-web-deps: ## Recreate the web dependency volumes (needed after a pnpm major bump)
	$(COMPOSE) rm -sf web >/dev/null 2>&1 || true
	@docker volume ls --format '{{.Name}}' \
		| grep -E '^$(PROJECT)_bower_.*(node_modules|next)$$' \
		| xargs -r docker volume rm >/dev/null
	@echo "web dependency volumes removed; the next make up reinstalls them"

prune-venvs: ## Remove the dangling anonymous venv volumes left by build/down cycles
	@vols="$$(docker volume ls -qf dangling=true -f 'name=^[0-9a-f]{64}$$')"; \
	[ -n "$$vols" ] || { echo "nothing to reclaim"; exit 0; }; \
	docker volume rm $$vols

logs: ## Tail all container logs
	@$(ADMIN) logs

logs-core: ## Tail the api's logs
	@$(ADMIN) logs core

logs-worker: ## Tail the fill provisioner + manual consumer logs
	@$(ADMIN) logs fill-provisioner fill-consumer

logs-jobs: ## Tail the jobs service's logs (the walks that queue a fill's or a backfill's runs)
	@$(ADMIN) logs jobs

logs-ingest: ## Tail the ingest worker's logs (row-push consume/dedupe/apply)
	@$(ADMIN) logs ingest-worker

logs-autofill: ## Tail the autofill provisioner + consumer logs (publish and run pushed rows' AI columns and the preview)
	@$(ADMIN) logs autofill-provisioner autofill-consumer preview-consumer

logs-cron: ## Tail the maintenance cron's logs
	@$(ADMIN) logs cron


logs-web: ## Tail the web dev servers' logs (app + marketing)
	@$(ADMIN) logs web
prune-preview-runs: ## Run the preview-run prune once, in the cron container (proves its environment)
	$(COMPOSE) exec cron uv run --frozen --package openbower-core python apps/core/manage.py prune_preview_runs
prune-webhook-deliveries: ## Run the webhook delivery prune once, in the cron container
	$(COMPOSE) exec cron uv run --frozen --package openbower-core python apps/core/manage.py prune_webhook_deliveries
flush-deferred: ## Run the deferred flush once, in the cron container (sends every digest that is due)
	$(COMPOSE) exec cron uv run --frozen --package openbower-core python apps/core/manage.py flush_deferred
run-jobs: ## Run one jobs tick by hand, in the jobs container (the service loops on its own)
	$(COMPOSE) exec jobs uv run --frozen --package openbower-core python apps/core/manage.py run_jobs --once

# A debugging aid outside the stack: a plain container on the stack's
# network, so it never rides `make up` or a deployment.
receiver: ## Start a throwaway webhook receiver with a UI (localhost:8085; one URL, http://webhook-receiver.localhost:8085, works from the browser and the stack)
	@./scripts/debug/webhook-receiver.sh

receiver-stop: ## Stop the throwaway webhook receiver
	@./scripts/debug/webhook-receiver.sh stop


local-exec: ## Run a command in a container (e.g. make local-exec SVC=core CMD="uv run ruff check .")
	@[ -n "$(SVC)" ] || { echo 'usage: make local-exec SVC=<service> CMD="<command>"'; exit 1; }
	$(COMPOSE) exec $(SVC) $(CMD)

local-manage: ## Run a Django manage.py command in the api container (e.g. make local-manage CMD=shell)
	$(COMPOSE) exec core uv run --frozen --package openbower-core python apps/core/manage.py $(CMD)

local-dbshell: ## Open a psql shell on the db service
	$(COMPOSE) exec db psql -U openbower openbower

test-core: db-up ## Run the app backend's suite (host-run, against the compose db)
	cd apps/core && DJANGO_ENV=test DJANGO_SECRET_KEY=test-only uv run python manage.py test

test-web: ## Run the web workspace's node tests (what CI runs)
	cd web && pnpm install --frozen-lockfile && pnpm -r test

test: test-core test-web ## Run every suite

schema: ## Regenerate the shared contract (schema.json, then the web zod)
	uv sync --all-packages
	uv run --no-sync python -m tools.schema_sync.generate
	cd web && pnpm --filter @bower/schema generate

schema-check: ## Fail if the committed contract is stale
	uv sync --all-packages
	uv run --no-sync python -m tools.schema_sync.generate --check
	cd web && pnpm --filter @bower/schema check
