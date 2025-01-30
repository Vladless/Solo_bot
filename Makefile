formatting:
	@echo "Running Ruff format..." && ruff format . --config pyproject.toml --exclude main.py,handlers/payments

	@echo "Running Ruff..." && ruff check . --config pyproject.toml --exclude main.py,handlers/payments --fix

lint:
	@echo "Running Ruff checks..." && ruff check . --config pyproject.toml --exclude main.py,handlers/payments