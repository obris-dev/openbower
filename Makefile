# COMPOSE_PROJECT_NAME in the environment OVERRIDES the compose file's
# own `name:`, which would split one checkout into two projects with
# their own containers and volumes. Passing -p makes the pin
# authoritative over the environment.
PROJECT := openbower
COMPOSE := docker compose -p $(PROJECT)
# Exported as well as passed, so a raw `docker compose` in any recipe or
# sub-shell resolves to the same project.
export COMPOSE_PROJECT_NAME := $(PROJECT)

.DEFAULT_GOAL := help
.PHONY: help hooks suite-network db-up up build down reset stop restart restart-core restart-worker restart-web reset-web-deps prune-venvs logs logs-core logs-worker logs-web local-exec local-manage local-dbshell test-core test-web test schema schema-check

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

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

db-up: suite-network ## Start just the database (what host-run tests need)
	$(COMPOSE) up -d --wait db

# The .env seed as a file rule, so every target that starts the stack
# gets it: without it core dies on the required DJANGO_SECRET_KEY, and
# env_file is deliberately optional so compose itself will not say why.
# ORDER-ONLY prerequisite (the `|`): seed only when the file is ABSENT.
# A normal prerequisite rebuilds whenever the example is newer, so a pull
# that touched .env.example would overwrite the operator's real keys.
apps/core/.env: | apps/core/.env.example
	@cp $(firstword $|) $@
	@echo "seeded $@ from .env.example (dev values; edit it for real credentials)"

# The cross-stack network the IdP and data service share with this stack
# (see docker-compose.yml). Idempotent, and created by whichever stack
# comes up first.
suite-network:
	@docker network inspect openbower-suite >/dev/null 2>&1 && exit 0; \
	docker network create openbower-suite >/dev/null 2>&1 \
		|| docker network inspect openbower-suite >/dev/null 2>&1 \
		|| { echo "could not create the openbower-suite network:"; \
		     docker network create openbower-suite >/dev/null; exit 1; }

# --wait-timeout covers the SERIAL cold path, not one term of it:
# core-setup syncs its own venv, then core syncs its own (they hold
# separate anonymous volumes), and `build` renews both by construction.
# A bound above that chain keeps a container stuck RESTARTING from
# blocking the target forever, without failing a start that is merely
# slow.
up: apps/core/.env suite-network ## Start the full local stack in Docker, detached (api :8002, app :3003, marketing :3005, fill worker)
	$(COMPOSE) up -d --wait --wait-timeout 900
	@echo "up: api :8002, app :3003, marketing :3005, fill worker (make logs to tail, make stop to stop)"

# Do not interrupt: a Ctrl-C while the worker is being recreated leaves it
# REMOVED with no policy to bring it back, and nothing else drains the
# fill queue. Recover with `make up`.
#
# --renew-anon-volumes: the anonymous .venv volume survives a recreate
# otherwise, so a freshly built image's venv would be masked by the old
# container's and the rebuild would deliver nothing.
build: apps/core/.env suite-network ## Rebuild after Dockerfile/dependency changes (waits for rows in flight, which can take minutes)
	$(COMPOSE) up --build -d --renew-anon-volumes --wait --wait-timeout 900

down: ## Stop and remove the stack's containers (the db's data and installed dependencies survive)
	$(COMPOSE) down

reset: ## Remove the stack AND its volumes (wipes the dev database and the installed dependencies)
	$(COMPOSE) down -v

stop: ## Stop the stack in place, waiting for the worker to finish rows in flight (make up resumes)
	$(COMPOSE) stop

# Compose-file edits are applied by RECREATING a service, which restart
# does not do: docker compose up -d --no-deps <service>. Without
# --no-deps that also re-runs core-setup, whose cold venv sync is what
# core's start period is sized for.
restart: ## Restart all services in place (compose-file edits need make up, which recreates)
	$(COMPOSE) restart

restart-core: ## Restart just the api
	$(COMPOSE) restart core

restart-worker: ## Restart just the fill worker (what a change to worker code needs; it has no reloader)
	$(COMPOSE) restart worker

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
	$(COMPOSE) logs -f

logs-core: ## Tail the api's logs
	$(COMPOSE) logs -f core

logs-worker: ## Tail the fill worker's logs
	$(COMPOSE) logs -f worker

logs-web: ## Tail the web dev servers' logs (app + marketing)
	$(COMPOSE) logs -f web

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
