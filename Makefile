formatting:
	@echo "Running black..." && black .
	@echo "Running isort..." && isort .
	@echo "Running flake8..." && flake8 --config .flake8
	@echo "Running pylint..." && pylint .
