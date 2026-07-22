# =============================================================================
# Loop — one entrypoint for every common task.
# Run `make help` to see everything. Designed so a new contributor can go from
# `git clone` to a running stack with two commands: `make setup` then `make up`.
# =============================================================================
.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------- Setup ----------
.PHONY: setup
setup: ## Install locked JavaScript and Python dependencies
	bash scripts/setup.sh
	-$(MAKE) sandbox-image
	@echo "Setup complete. Run 'make up' to start the stack."

.PHONY: sandbox-image
sandbox-image: ## Build the container-isolation image (needs Docker running)
	@docker info >/dev/null 2>&1 \
		&& docker build -f apps/api/sandbox.Dockerfile -t loop-sandbox:latest . \
		|| echo "Docker not running — skipping sandbox image (tasks will run inline)."

# ---------- Local stack (Docker) ----------
.PHONY: up
up: ## Start the full stack (web, api, postgres, redis) via docker compose
	@test -f .env || cp .env.example .env  # docker-only path needs no `make setup` first
	$(COMPOSE) up --build

.PHONY: up-d
up-d: ## Start the stack in the background
	@test -f .env || cp .env.example .env
	$(COMPOSE) up --build -d

.PHONY: down
down: ## Stop the stack and remove containers
	$(COMPOSE) down

.PHONY: clean-volumes
clean-volumes: ## Stop the stack and DELETE data volumes (destructive)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f

# ---------- Dev (no containers) ----------
.PHONY: dev
dev: ## Run web + api in watch mode locally (requires `make setup`)
	bash scripts/dev.sh

.PHONY: verify-receipt
verify-receipt: ## Independently verify a Receipt: make verify-receipt f=path/to/receipt.json
	cd apps/api && python scripts/verify_receipt.py $(f)

.PHONY: receipt-keygen
receipt-keygen: ## Generate a Receipt signing key -> receipt_signing_key.pem (set AGENT_RECEIPT_SIGNING_KEY_FILE)
	cd apps/api && . .venv/bin/activate && python -c "from pathlib import Path; from app.services.skills import generate_keypair; priv,pub=generate_keypair(); p=Path('receipt_signing_key.pem'); p.write_text(priv); p.chmod(0o600); print('wrote apps/api/receipt_signing_key.pem — set AGENT_RECEIPT_SIGNING_KEY_FILE=./receipt_signing_key.pem to sign Receipts')"

.PHONY: authority-keygen
authority-keygen: ## Generate Worker authority issuer + gateway/proxy verifier keys
	cd apps/api && . .venv/bin/activate && python -c "from pathlib import Path; from app.domain.authority_token import authority_key_id; from app.services.skills import generate_keypair; priv,pub=generate_keypair(); p=Path('authority_signing_key.pem'); p.write_text(priv); p.chmod(0o600); Path('authority_public_key.pem').write_text(pub); print('wrote apps/api/authority_signing_key.pem (0600) and apps/api/authority_public_key.pem'); print('authority key id:', authority_key_id(pub))"

.PHONY: skill-keygen
skill-keygen: ## Generate a skill signing keypair: make skill-keygen out=.
	cd apps/api && . .venv/bin/activate && python scripts/skill_tool.py keygen $(or $(out),.)

.PHONY: skill-sign
skill-sign: ## Sign a skill: make skill-sign dir=skills/foo key=signing_key.pem
	cd apps/api && . .venv/bin/activate && python scripts/skill_tool.py sign $(dir) $(key)

.PHONY: demo
demo: ## Start the zero-key verified demo (web + API)
	bash scripts/demo.sh

.PHONY: repository-eval-isolated
repository-eval-isolated: ## Run the full-Loop repository matrix with required Docker isolation
	bash scripts/evaluate-repository-matrix-isolated.sh $(args)

# ---------- Database ----------
.PHONY: migrate
migrate: ## Apply all database migrations
	cd apps/api && . .venv/bin/activate && alembic upgrade head

.PHONY: migration
migration: ## Create a new migration: make migration m="add users"
	cd apps/api && . .venv/bin/activate && alembic revision --autogenerate -m "$(m)"

# ---------- Quality gates ----------
.PHONY: lint
lint: ## Lint everything (JS + Python)
	pnpm lint
	cd apps/api && . .venv/bin/activate && ruff check . && ruff format --check .

.PHONY: format
format: ## Auto-format everything
	pnpm format
	cd apps/api && . .venv/bin/activate && ruff format . && ruff check --fix .

.PHONY: typecheck
typecheck: ## Static type checks (tsc + mypy)
	pnpm typecheck
	cd apps/api && . .venv/bin/activate && mypy app

.PHONY: test
test: ## Run all tests
	pnpm test
	cd apps/api && . .venv/bin/activate && pytest

.PHONY: check
check: lint typecheck test ## Run the full quality gate (what CI runs)

.PHONY: audit
audit: ## Audit production Python and JavaScript dependencies
	pnpm audit --prod
	cd apps/api && . .venv/bin/activate && pip-audit

.PHONY: enforcement-acceptance
enforcement-acceptance: ## Verify Redis restart, worker recovery, fail-closed readiness, and revocation
	bash scripts/enforcement-acceptance.sh

# ---------- Kubernetes ----------
.PHONY: k8s-dev
k8s-dev: ## Render the dev overlay (kustomize) to stdout
	kubectl kustomize infra/k8s/overlays/dev

.PHONY: k8s-apply-dev
k8s-apply-dev: ## Apply the dev overlay to the current kube-context
	kubectl apply -k infra/k8s/overlays/dev

.PHONY: k8s-enforcement-smoke
k8s-enforcement-smoke: ## Verify rollout and enforcement NetworkPolicy in a deployed namespace
	bash scripts/k8s-enforcement-smoke.sh $(or $(namespace),loop-prod)

.PHONY: k8s-deployment-acceptance
k8s-deployment-acceptance: ## Build and verify production mode in a disposable k3d cluster
	bash scripts/k8s-deployment-acceptance.sh
