# Quick commands

.PHONY: run setup run-only

setup:
	@echo "[setup] Creating venv and installing deps..."
	@bash scripts/run_bot.sh || true

run:
	@bash scripts/run_bot.sh

run-only:
	@bash scripts/run_only.sh


