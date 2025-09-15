format:
	@echo "Running Ruff format..." && ruff format . --config pyproject.toml --exclude main.py,handlers/payments
	@echo "Running Ruff..." && ruff check . --config pyproject.toml --exclude main.py,handlers/payments --fix

lint:
	@echo "Running Ruff checks..." && ruff check . --config pyproject.toml --exclude main.py,handlers/payments

format-payments:
	@echo "Running Ruff format ONLY on handlers/payments..." && ruff format handlers/payments --config pyproject.toml
	@echo "Running Ruff check ONLY on handlers/payments..." && ruff check handlers/payments --config pyproject.toml --fix
