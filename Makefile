UV := $(HOME)/.local/bin/uv
VENV_PYTHON := .venv/bin/python
APP := .venv/bin/clockify-cli

.PHONY: install run test lint reinstall

# Full install (non-editable, copies to site-packages — works on iCloud Drive)
install:
	$(UV) pip install .

# Reinstall after code changes
reinstall:
	$(UV) pip install --reinstall .

# Launch the TUI
run: reinstall
	$(UV) run clockify-cli

# Run all tests
test:
	$(UV) run pytest tests/ -v

# Lint
lint:
	$(UV) run ruff check clockify_cli/ tests/
