#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .venv/bin/activate ]]; then
  echo "[error] Virtual env not found. Please run scripts/run_bot.sh first." >&2
  exit 1
fi

source .venv/bin/activate
export PYTHONPATH=src
python -c "from auto_bot import run; run()"


