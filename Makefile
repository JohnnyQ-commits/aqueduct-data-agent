.PHONY: help install dev test lint format check clean validate

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install package
	pip install -e .

dev:  ## Install with dev dependencies
	pip install -e ".[dev]"

test:  ## Run tests
	python -m pytest tests/ -v

test-cov:  ## Run tests with coverage report
	python -m pytest tests/ --cov=src/aqueduct --cov-report=html

test-quick:  ## Run tests (quiet mode)
	python -m pytest tests/ -q --tb=short

lint:  ## Run linter
	ruff check src/ tests/

format:  ## Format code
	ruff format src/ tests/

check: lint  ## Run all checks (lint + format check)
	ruff format --check src/ tests/

clean:  ## Clean build artifacts and caches
	rm -rf build/ dist/ *.egg-info .pytest_cache/ .ruff_cache/ htmlcov/ .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

validate:  ## Validate a SQL file (usage: make validate FILE=query.sql)
	python -m aqueduct.cli.main validate $(FILE) --strict
