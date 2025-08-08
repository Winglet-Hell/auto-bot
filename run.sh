#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "--only" ]]; then
  bash scripts/run_only.sh
else
  bash scripts/run_bot.sh
fi


