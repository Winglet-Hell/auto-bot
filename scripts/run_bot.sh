#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Prefer Python 3.12 if available
PY=python3
if command -v python3.12 >/dev/null 2>&1; then
  PY=python3.12
fi

# Create venv if missing
if [[ ! -x .venv/bin/python ]]; then
  echo "[setup] Creating virtual environment with $PY..."
  $PY -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null

if [[ -f requirements.txt ]]; then
  echo "[setup] Installing requirements..."
  python -m pip install -r requirements.txt
else
  python -m pip install playwright python-dotenv
fi

# Ensure Playwright browser
python -m playwright install chromium

# Run GUI
export PYTHONPATH=src
python -c "from auto_bot import run; run()"


