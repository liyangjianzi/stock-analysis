#!/usr/bin/env bash
# Run the stock-analysis pipeline as a job (cron/launchd-friendly).
#
# Usage:
#   scripts/run_daily.sh                  # defaults below
#   scripts/run_daily.sh --top 10         # extra args are passed straight to `stock-analysis run`
#
# Cron example (weekdays 5pm):
#   0 17 * * 1-5 /Users/liyanglu/PycharmProjects/StockAnalysis/scripts/run_daily.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

source venv/bin/activate

mkdir -p logs
LOG_FILE="logs/run_$(date +%Y%m%d_%H%M%S).log"

stock-analysis run --target excel --out output/ "$@" \
  > >(tee -a "$LOG_FILE") 2>&1
