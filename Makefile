.DEFAULT_GOAL := help
.PHONY: help hooks

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
