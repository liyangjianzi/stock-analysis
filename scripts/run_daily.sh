#!/usr/bin/env bash
# Run the stock-analysis pipeline as a job (cron/launchd-friendly).
#
# Usage:
#   scripts/run_daily.sh                  # defaults below
#   scripts/run_daily.sh --top 10         # extra args are passed straight to `stock-analysis run`
#   ACCOUNT=50000 scripts/run_daily.sh    # size this run against a 50k account
#
# Cron example (weekdays 5pm):
#   0 17 * * 1-5 /Users/liyanglu/PycharmProjects/StockAnalysis/scripts/run_daily.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# Optional local overrides, kept out of git so a real account size never gets
# committed. Create scripts/run_daily.env with lines like: ACCOUNT=50000
if [[ -f scripts/run_daily.env ]]; then
  # shellcheck disable=SC1091
  source scripts/run_daily.env
fi

# Risk / sizing inputs for the trade plan. These are PERCENTS, not fractions —
# RISK_PCT=1.0 means 1% of the account per trade (the library API takes 0.01).
#
# Deliberately NOT defaulted here: an unset variable appends no flag, so the run
# inherits the single source of truth in config.py / signals.py via the CLI's own
# argparse defaults. Restating them in shell would let a scheduled job silently
# keep sizing at the old numbers after config.py changed.
args=()
[[ -n "${ACCOUNT:-}" ]]     && args+=(--account "$ACCOUNT")
[[ -n "${RISK_PCT:-}" ]]    && args+=(--risk-pct "$RISK_PCT")
[[ -n "${MAX_WEIGHT:-}" ]]  && args+=(--max-weight "$MAX_WEIGHT")
[[ -n "${FUND_MIN:-}" ]]    && args+=(--fund-min "$FUND_MIN")

source venv/bin/activate

mkdir -p logs
LOG_FILE="logs/run_$(date +%Y%m%d_%H%M%S).log"

# "$@" goes last on purpose: argparse lets a repeated flag win, so a caller can
# override any default above, e.g. `scripts/run_daily.sh --account 25000`.
stock-analysis run \
  --target excel --out output/ \
  ${args[@]+"${args[@]}"} \
  "$@" \
  > >(tee -a "$LOG_FILE") 2>&1
