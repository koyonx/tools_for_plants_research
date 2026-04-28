# =============================================================================
# tools_for_plants_research — operator Makefile
#
# `make help` to discover targets.  Targets prefixed `## ` in the source
# below are auto-listed; keep one short sentence per line.
# =============================================================================

SHELL := bash
COMPOSE := docker compose
BACKEND_SVC := backend
FRONTEND_SVC := frontend
DB_SVC := supabase-db
ENV_FILE := .env

# Colour helpers for `make help`
BOLD := $(shell tput bold 2>/dev/null)
RESET := $(shell tput sgr0 2>/dev/null)

.DEFAULT_GOAL := help

# ----------------------------------------------------------------------------
# Bootstrap & environment
# ----------------------------------------------------------------------------

.PHONY: help
help: ## Show this list of targets
	@echo "$(BOLD)tools_for_plants_research — make targets$(RESET)"
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_.-]+:.*## / { printf "  $(BOLD)%-22s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST) | sort

.PHONY: init-env
init-env: ## Create .env from .env.example if it doesn't exist
	@[ -f $(ENV_FILE) ] && echo "$(ENV_FILE) exists, leaving it alone" || cp .env.example $(ENV_FILE)

.PHONY: gen-jwt
gen-jwt: ## Print Supabase anon/service JWTs for the JWT_SECRET in .env
	@./scripts/generate-jwt.sh "$$(grep ^JWT_SECRET $(ENV_FILE) | cut -d= -f2-)"

.PHONY: setup-local
setup-local: ## One-shot: write .env with auto-generated secrets so `make up` just works
	@bash ./scripts/setup-local.sh

.PHONY: login
login: ## Print a working magic-link URL.  Usage: make login EMAIL=you@example.com [NEXT=/dashboard]
	@if [ -z "$(EMAIL)" ]; then echo "usage: make login EMAIL=you@example.com [NEXT=/dashboard]" >&2; exit 1; fi
	@./scripts/magic-link.sh "$(EMAIL)" "$(or $(NEXT),/dashboard)"

# ----------------------------------------------------------------------------
# Stack lifecycle
# ----------------------------------------------------------------------------

.PHONY: up
up: ## Start every service (detached)
	$(COMPOSE) up -d

.PHONY: down
down: ## Stop every service, keep volumes
	$(COMPOSE) down

.PHONY: down-volumes
down-volumes: ## Stop every service AND drop volumes (DB reset, model cache reset)
	$(COMPOSE) down -v

.PHONY: restart
restart: ## Restart every service
	$(COMPOSE) restart

.PHONY: restart-backend
restart-backend: ## Restart just the FastAPI backend (picks up code changes)
	$(COMPOSE) restart $(BACKEND_SVC)

.PHONY: restart-frontend
restart-frontend: ## Restart just the Next.js frontend
	$(COMPOSE) restart $(FRONTEND_SVC)

.PHONY: build
build: ## Rebuild backend + frontend images
	$(COMPOSE) build $(BACKEND_SVC) $(FRONTEND_SVC)

.PHONY: rebuild
rebuild: ## Force rebuild from scratch (no cache)
	$(COMPOSE) build --no-cache $(BACKEND_SVC) $(FRONTEND_SVC)

.PHONY: bootstrap
bootstrap: ## Re-run the supabase-bootstrap migration container (idempotent)
	$(COMPOSE) up -d --force-recreate --no-deps supabase-bootstrap

.PHONY: ps
ps: ## Show running services
	$(COMPOSE) ps

# ----------------------------------------------------------------------------
# Logs
# ----------------------------------------------------------------------------

.PHONY: logs
logs: ## Tail logs for every service
	$(COMPOSE) logs -f --tail=100

.PHONY: logs-backend
logs-backend: ## Tail backend logs
	$(COMPOSE) logs -f --tail=200 $(BACKEND_SVC)

.PHONY: logs-frontend
logs-frontend: ## Tail frontend logs
	$(COMPOSE) logs -f --tail=200 $(FRONTEND_SVC)

.PHONY: logs-db
logs-db: ## Tail Postgres logs
	$(COMPOSE) logs -f --tail=200 $(DB_SVC)

.PHONY: logs-auth
logs-auth: ## Tail GoTrue logs (where magic-link URLs surface in dev)
	$(COMPOSE) logs -f --tail=200 supabase-auth

# ----------------------------------------------------------------------------
# Shells
# ----------------------------------------------------------------------------

.PHONY: shell-backend
shell-backend: ## Bash inside the backend container
	$(COMPOSE) exec $(BACKEND_SVC) bash

.PHONY: shell-frontend
shell-frontend: ## sh inside the frontend container
	$(COMPOSE) exec $(FRONTEND_SVC) sh

.PHONY: psql
psql: ## psql shell into the Supabase Postgres
	$(COMPOSE) exec $(DB_SVC) psql -U postgres -d postgres

# ----------------------------------------------------------------------------
# Quality
# ----------------------------------------------------------------------------

.PHONY: lint
lint: lint-backend lint-frontend ## Lint backend (ruff) + frontend (biome)

.PHONY: lint-fix
lint-fix: ## Auto-fix lint where possible (ruff + biome)
	cd backend && ruff check --fix app tests
	cd frontend && npx biome check --write .

.PHONY: lint-backend
lint-backend:
	cd backend && ruff check app tests

.PHONY: lint-frontend
lint-frontend:
	cd frontend && npm run lint

.PHONY: typecheck
typecheck: typecheck-backend typecheck-frontend ## mypy + tsc

.PHONY: typecheck-backend
typecheck-backend:
	cd backend && mypy app

.PHONY: typecheck-frontend
typecheck-frontend:
	cd frontend && npm run typecheck

.PHONY: test
test: test-backend ## Run all unit tests

.PHONY: test-backend
test-backend:
	cd backend && pytest -q

.PHONY: format
format: ## Auto-format backend (ruff format) + frontend (biome format)
	cd backend && ruff format app tests
	cd frontend && npx biome format --write .

.PHONY: check
check: lint typecheck test ## Full quality gate (what CI runs)

# ----------------------------------------------------------------------------
# Smoke + validation
# ----------------------------------------------------------------------------

.PHONY: smoke
smoke: ## Boot the stack + poll /health, /analyze/segformer/status, frontend
	@$(COMPOSE) up -d
	@echo "polling backend /health (cold start can take a minute on first run)…"
	@for i in $$(seq 1 60); do \
		curl -fsS http://localhost:8001/health >/dev/null 2>&1 && break; \
		sleep 2; \
	done
	@printf "/health → "; curl -fsS http://localhost:8001/health || (echo FAIL; exit 1); echo
	@printf "/analyze/segformer/status → "; curl -fsS http://localhost:8001/analyze/segformer/status || true; echo
	@printf "frontend / → "; curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:3000

.PHONY: stop
stop: ## Pause every service without removing containers (resume with `make up`)
	$(COMPOSE) stop

# `python3` rather than `python` — modern macOS (and many Linux distros)
# only ship the versioned binary.  Override with `make validate PYTHON=python`
# if you're pinned to a venv where the unversioned name works.
PYTHON ?= python3

.PHONY: validate
validate: ## Compare basic_measurement output against ../measure_results.xlsx
	@command -v $(PYTHON) >/dev/null || { \
		echo "error: $(PYTHON) not found.  Override with 'make validate PYTHON=python'."; \
		exit 1; \
	}
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { \
		echo "error: $(PYTHON) is too old (need 3.11+).  On macOS CLT (3.9), install a newer interpreter:"; \
		echo "  brew install python@3.12 && make validate PYTHON=python3.12"; \
		exit 1; \
	}
	@# Source .env first so VALIDATE_EMAIL / VALIDATE_TOKEN / ANON_KEY /
	@# SUPABASE_PUBLIC_URL can all come from the file the rest of the
	@# stack already uses.  Guard for at least one credential env var
	@# *after* sourcing so files-only configurations count.
	@set -a && [ -f $(ENV_FILE) ] && . $(ENV_FILE); set +a; \
	if [ -z "$$VALIDATE_EMAIL$$VALIDATE_TOKEN" ]; then \
		echo "error: set VALIDATE_EMAIL=you@example.com (password account) or VALIDATE_TOKEN=<jwt> (paste from devtools)"; \
		echo "       (may be provided inline, in the calling shell, or in $(ENV_FILE))"; \
		exit 1; \
	fi; \
	if [ -n "$$VALIDATE_TOKEN" ]; then \
		$(PYTHON) scripts/validate_against_xlsx.py \
			--xlsx ../measure_results.xlsx \
			--reference-um 100 \
			--access-token "$$VALIDATE_TOKEN"; \
	else \
		$(PYTHON) scripts/validate_against_xlsx.py \
			--xlsx ../measure_results.xlsx \
			--reference-um 100 \
			--user-email "$$VALIDATE_EMAIL"; \
	fi

.PHONY: compose-config
compose-config: ## Validate the docker-compose YAML chain
	$(COMPOSE) config --quiet && echo "compose OK"

# ----------------------------------------------------------------------------
# ML helpers
# ----------------------------------------------------------------------------

.PHONY: warm-cellpose
warm-cellpose: ## Pre-download the Cellpose cyto3 weights into the named volume
	$(COMPOSE) exec $(BACKEND_SVC) python -c "from cellpose import models; models.CellposeModel(pretrained_model='cyto3', gpu=False)"

.PHONY: notebook
notebook: ## Open the SegFormer training notebook in Jupyter (host-side)
	cd notebooks && jupyter notebook segformer_train.ipynb

# ----------------------------------------------------------------------------
# Misc
# ----------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove local build artefacts (Python caches, .next, node_modules of frontend)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	rm -rf frontend/.next frontend/tsconfig.tsbuildinfo

.PHONY: clean-frontend-modules
clean-frontend-modules: ## Wipe frontend/node_modules (force-reinstall on next build)
	rm -rf frontend/node_modules
