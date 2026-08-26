.DEFAULT_GOAL := help
.PHONY: help hooks db-up api-local web-local worker-local local stop logs test-core test-web test schema schema-check

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

db-up: ## Start the databases (docker compose) and wait for health
	docker compose up -d --wait

api-local: db-up ## Run the app backend on :8002, attached (migrate + cache table first)
	cd apps/core && uv run python manage.py migrate \
		&& uv run python manage.py createcachetable \
		&& uv run python manage.py runserver 8002

web-local: ## Run the web app on :3003, attached
	cd web/apps/app && pnpm dev

worker-local: db-up ## Run the fill worker attached (SIGINT finishes rows in flight)
	cd apps/core && uv run python manage.py fill_worker

# Matching processes, MINUS this recipe's own shell. Make runs a whole
# recipe line as `sh -c "<line>"`, so that line's own text (the pattern,
# and the start command beside it) is in the shell's command line and
# pgrep -f matches it. BSD pgrep hides this by excluding its ancestors;
# procps (Linux) does not, so the guard below found itself and
# `make local` would start no worker at all on a clean Linux box.
# Excluding $$ makes both platforms agree.
pids = pgrep -f "$(1)" | grep -vx "$$$$"
running = $(call pids,$(1)) | grep -q .
# Kill matching processes, skipping any SHELL that merely carries the
# pattern in its own command line. `while read` forks a subshell of
# the recipe shell, which inherits that whole command line and is
# pgrep's SIBLING rather than its ancestor, so neither BSD's ancestor
# exclusion nor the $$ filter above removes it: the loop SIGTERMs
# itself and the real processes survive. Matching on the executable
# instead of the pid answers "is this actually a service" directly.
#
# A DENY list, not an allow list: comm is settable (a runtime that
# renames itself would survive an allow list and leave the port held,
# which is the failure you only find on the next start). Skipping
# shells fails toward killing instead, which is safe because the
# pattern above is already specific to one service.
kill_matching = $(call pids,$(1)) | while read pid; do \
		case "$$(ps -o comm= -p $$pid 2>/dev/null)" in \
			""|*sh) ;; \
			*) kill "$$pid" 2>/dev/null || true ;; \
		esac; \
	done

local: db-up ## Start everything DETACHED (api :8002, web :3003, fill worker; logs in /tmp/openbower)
	@mkdir -p /tmp/openbower
	cd apps/core && uv run python manage.py migrate --no-input >/dev/null \
		&& uv run python manage.py createcachetable >/dev/null
# The API RELOADS here: this profile is a checkout running on your own
# machine, so an edit is meant to be live without a restart. The fill
# worker below does not, and cannot: a custom command gets no reloader,
# and a reloader kills and respawns, which would drop a row mid flight
# against a metered provider. So a change to WORKER code still needs a
# restart, and a restart resumes whatever fill was live.
	cd apps/core && (nohup uv run python manage.py runserver 8002 > /tmp/openbower/api.log 2>&1 &)
	@$(call running,manage.py fill_worker) \
		&& echo "fill worker already running; not starting a second (ONE per deploy: each enforces a source's declared ceiling on its own, so two double the load on that box)" \
		|| (cd apps/core && nohup uv run python manage.py fill_worker > /tmp/openbower/worker.log 2>&1 &)
	cd web/apps/app && (nohup pnpm dev > /tmp/openbower/web.log 2>&1 &)
	@echo "up: api :8002, web :3003, fill worker (make logs to tail, make stop to stop)"

stop: ## Stop the detached services (containers keep running; docker compose stop for those)
	-@$(call kill_matching,manage.py runserver 8002)
	-@$(call kill_matching,manage.py fill_worker)
	-@$(call kill_matching,next dev.*3003)

logs: ## Tail the detached services' logs
	tail -n 40 -F /tmp/openbower/api.log /tmp/openbower/worker.log /tmp/openbower/web.log

test-core: ## Run the app backend's suite
	cd apps/core && DJANGO_ENV=test DJANGO_SECRET_KEY=test-only uv run python manage.py test

test-web: ## Run the web workspace's node tests (what CI runs)
	cd web && pnpm -r test

test: test-core test-web ## Run every suite

schema: ## Regenerate the shared contract (schema.json, then the web zod)
	uv run python -m tools.schema_sync.generate
	cd web && pnpm --filter @bower/schema generate

schema-check: ## Fail if the committed contract is stale
	uv run python -m tools.schema_sync.generate --check
	cd web && pnpm --filter @bower/schema check
