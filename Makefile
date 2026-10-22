.DEFAULT_GOAL := help
COMPOSE ?= docker compose

# Tests run on the host against the compose database (app_test).
TEST_ENV = TEST_SUPERUSER_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/app_test

.PHONY: help install up down logs migrate seed test cov lint fmt typecheck check

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install dependencies into .venv
	uv sync --frozen

up: ## Start postgres, run migrations and start the API on :8000
	$(COMPOSE) up -d --build --wait db api

down: ## Stop the stack and delete the database volume
	$(COMPOSE) down -v

logs: ## Tail API logs
	$(COMPOSE) logs -f api

migrate: ## Apply migrations to the dev database
	$(COMPOSE) run --rm migrate alembic upgrade head

seed: ## Create demo tenants (password: demo-password-123)
	$(COMPOSE) run --rm migrate python -m app.cli seed-demo

test: ## Run the test suite with coverage (needs `make up` or a local Postgres)
	$(TEST_ENV) uv run pytest --cov --cov-report=term-missing --cov-fail-under=85

lint: ## Ruff lint + format check + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt: ## Auto-format and fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

check: lint test ## Everything CI runs
