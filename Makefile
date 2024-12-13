formatting:
	@echo "Running Ruff..." && ruff check . --fix
	@echo "Running Ruff format..." && ruff format .

lint:
	@echo "Running Ruff checks..." && ruff check .