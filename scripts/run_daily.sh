#!/usr/bin/env bash
# Run the stock-analysis daily job (cron/launchd-friendly).
#
# Two steps, in priority order:
#   1. the signal pipeline  -> output/<timestamp>/{signal_matrix.xlsx,report.html}
#   2. a research-cache top-up so `backtest --universe` stays current
#
# Usage:
#   scripts/run_daily.sh                  # both steps
#   scripts/run_daily.sh --top 10         # extra args go to `stock-analysis run`
#   ACCOUNT=50000 scripts/run_daily.sh    # size this run against a 50k account
#   SKIP_CACHE=1 scripts/run_daily.sh     # pipeline only
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

UNIVERSE_CSV="${UNIVERSE_CSV:-data/universe_sp500.csv}"

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
# Tee everything below, so both steps land in one log rather than only step 1.
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== $(date '+%Y-%m-%d %H:%M:%S')  stock-analysis daily ==="

# 1) Signals. "$@" goes last on purpose: argparse lets a repeated flag win, so a
#    caller can override any default above, e.g. `run_daily.sh --account 25000`.
echo "--- pipeline ---"
stock-analysis run \
  --target excel --out output/ \
  ${args[@]+"${args[@]}"} \
  "$@"

# 2) Research cache. Incremental by default — already-cached tickers get a short
#    top-up window, so this is seconds rather than a re-download of the universe.
#    Non-fatal on purpose: a failed refresh must not mask a successful pipeline
#    run, which is the output that is actually time-sensitive.
if [[ -n "${SKIP_CACHE:-}" ]]; then
  echo "--- cache refresh skipped (SKIP_CACHE set) ---"
elif [[ ! -f "$UNIVERSE_CSV" ]]; then
  echo "--- cache refresh skipped: $UNIVERSE_CSV not found ---"
  echo "    create it with: stock-analysis universe --out $UNIVERSE_CSV"
else
  echo "--- cache refresh ($UNIVERSE_CSV) ---"
  stock-analysis cache --universe "$UNIVERSE_CSV" \
    || echo "WARN: cache refresh failed; pipeline output above is unaffected."
fi

echo "=== done $(date '+%H:%M:%S') ==="
