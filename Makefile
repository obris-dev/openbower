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

local: db-up ## Start everything DETACHED (api :8002, web :3003, fill worker; logs in /tmp/openbower)
	@mkdir -p /tmp/openbower
	cd apps/core && uv run python manage.py migrate --no-input >/dev/null \
		&& uv run python manage.py createcachetable >/dev/null
	cd apps/core && (nohup uv run python manage.py runserver 8002 --noreload > /tmp/openbower/api.log 2>&1 &)
	@$(call running,manage.py fill_worker) \
		&& echo "fill worker already running; not starting a second (ONE per deploy: each enforces a source's declared ceiling on its own, so two double the load on that box)" \
		|| (cd apps/core && nohup uv run python manage.py fill_worker > /tmp/openbower/worker.log 2>&1 &)
	cd web/apps/app && (nohup pnpm dev > /tmp/openbower/web.log 2>&1 &)
	@echo "up: api :8002, web :3003, fill worker (make logs to tail, make stop to stop)"

stop: ## Stop the detached services (containers keep running; docker compose stop for those)
	-@$(call pids,manage.py runserver 8002) | while read pid; do kill "$$pid" 2>/dev/null || true; done
	-@$(call pids,manage.py fill_worker) | while read pid; do kill "$$pid" 2>/dev/null || true; done
	-@$(call pids,next dev.*3003) | while read pid; do kill "$$pid" 2>/dev/null || true; done

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
