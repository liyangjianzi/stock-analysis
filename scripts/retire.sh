#!/usr/bin/env bash
# Run the Canadian retirement analysis and open its report.
#
#   retirement/plan.json  ->  retirement/output/<timestamp>/retirement_report.html + summary.json
#
# The plan and its reports live in the gitignored retirement/ folder (the repo is
# public). Balances come from plan.json "balances" if present, otherwise from the
# holdings workbook (data/holdings_workbook.xlsx) — ask Claude to "refresh
# holdings" first so the run uses current balances.
#
# Usage:
#   scripts/retire.sh                      # run the plan and open the report
#   scripts/retire.sh --paths 2000         # extra args go to `stock-analysis retire`
#   scripts/retire.sh --holdings FILE      # read balances from another holdings file
#   NO_OPEN=1 scripts/retire.sh            # don't open the report in the browser
#   scripts/retire.sh --init               # first time only: write a starter plan.json
#   stock-analysis retire --gui            # edit plan.json in a local web page instead
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

source venv/bin/activate

# The log holds the plan's results, so it lives with the plan in the gitignored
# retirement/ folder: every personal retirement file stays in one place.
mkdir -p retirement/logs
LOG_FILE="retirement/logs/retire_$(date +%Y%m%d_%H%M%S).log"

stock-analysis retire "$@" 2>&1 | tee "$LOG_FILE"
status=${PIPESTATUS[0]}
if [[ $status -ne 0 ]]; then
  echo "retire failed (exit $status); see $LOG_FILE" >&2
  exit "$status"
fi

REPORT="$(grep -m1 '^Report: ' "$LOG_FILE" | sed 's/^Report: //' || true)"
if [[ -n "$REPORT" && -z "${NO_OPEN:-}" ]] && command -v open >/dev/null 2>&1; then
  open "$REPORT"
fi
