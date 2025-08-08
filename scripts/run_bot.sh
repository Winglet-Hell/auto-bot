#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Create venv if missing
if [[ ! -x .venv/bin/python3 ]]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip >/dev/null

if [[ -f requirements.txt ]]; then
  echo "[setup] Installing requirements..."
  python -m pip install -r requirements.txt
else
  python -m pip install playwright python-dotenv
fi

python -m playwright install chromium

# Run module entrypoint
PYTHONPATH=. python -c "from src.auto_bot import run; run()"


