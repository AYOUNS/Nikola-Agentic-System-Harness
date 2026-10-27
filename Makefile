.PHONY: lint lint-fix test test-unit test-e2e test-integration install install-dev

install:
	PIP_CONFIG_FILE=/dev/null pip install --index-url https://pypi.org/simple -r requirements.txt

install-dev: install
	PIP_CONFIG_FILE=/dev/null pip install --index-url https://pypi.org/simple ruff pytest

lint:
	PIP_CONFIG_FILE=/dev/null pip install --quiet --index-url https://pypi.org/simple ruff
	ruff check agent/ tests/

lint-fix:
	PIP_CONFIG_FILE=/dev/null pip install --quiet --index-url https://pypi.org/simple ruff
	ruff check --fix agent/ tests/

test-unit:
	pytest tests/unit/ -v --tb=short

test-e2e:
	@if [ -f .env ]; then export $$(grep -v '^#' .env | xargs); fi; \
	pytest tests/e2e/ -v --tb=short

test-integration:
	@if [ -f .env ]; then export $$(grep -v '^#' .env | xargs); fi; \
	pytest tests/integration/ -v --tb=short

test: test-unit test-e2e test-integration
