.PHONY: format lint format-payments hooks test test-sudo smoke

format:
	@echo "Running Ruff format..." && ruff format . --config pyproject.toml --exclude main.py,handlers/payments
	@echo "Running Ruff..." && ruff check . --config pyproject.toml --exclude main.py,handlers/payments --fix

lint:
	@echo "Running Ruff checks..." && ruff check . --config pyproject.toml --exclude main.py,handlers/payments

# Ставит git-хуки: автоформат staged-файлов на commit, проверка формата на push.
hooks:
	@bash "$(CURDIR)/scripts/install-hooks.sh"

format-payments:
	@echo "Running Ruff format ONLY on handlers/payments..." && ruff format handlers/payments --config pyproject.toml
	@echo "Running Ruff check ONLY on handlers/payments..." && ruff check handlers/payments --config pyproject.toml --fix

# -t обязателен: без него unittest не считает tests пакетом и не выполняет tests/__init__.py,
# где отключается отправка сообщений в Telegram.
test:
	@echo "Running unit tests..." && cd /tmp && PYTHONPATH="$(CURDIR)" "$(CURDIR)/venv/bin/python" -m unittest discover -s "$(CURDIR)/tests" -t "$(CURDIR)" -q

test-sudo:
	@echo "Running unit tests with sudo..." && cd /tmp && sudo env PYTHONPATH="$(CURDIR)" "$(CURDIR)/venv/bin/python" -m unittest discover -s "$(CURDIR)/tests" -t "$(CURDIR)" -q

smoke:
	@echo "Running smoke checks..." && bash "$(CURDIR)/tests/smoke_runner.sh"
