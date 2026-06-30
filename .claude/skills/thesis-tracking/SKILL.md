---
name: thesis-tracking
description: Track investment theses across their lifecycle in the StockAnalysis project — register an idea (from a pipeline signal_matrix or by hand), move it through entry → exit, and generate P&L postmortems. Use when the user says "register thesis", "track this idea", "thesis status / review due", "open/close position", "postmortem", "trading journal", or wants to record/review what they bought and why. Native to this repo (no external API key) — it is the `stockanalysis.thesis` subpackage + the `stock-analysis thesis` CLI.
---

# Thesis Tracking

A lean, native port of `trader-memory-core` for the StockAnalysis package. The
signal engine answers *"what looks good today"*; this answers *"what did I act
on, why, when do I review it, and how did it turn out"* — the Plan → Trade →
Record → Review → Improve loop. Storage is JSON-per-thesis under `data/theses/`
(stdlib only); MAE/MFE comes from the same `yfinance` source the package
already uses, so **no FMP/paid key is needed**.

## When to use

- "Register / track this idea", ingest today's Buys into the journal.
- "What's due for review?", "thesis status", "list my open positions".
- Recording a fill, a trim, or a close; killing a broken thesis.
- "Write a postmortem", "how have my closed trades done" (summary stats).

This skill **does not** give buy/sell advice or place orders — it records and
reviews decisions the user makes.

## Lifecycle (forward-only)

```
IDEA → ENTRY_READY → ACTIVE → PARTIALLY_CLOSED → CLOSED
   └──────────────────(any non-terminal)──────────────→ INVALIDATED
```

Terminal = `CLOSED` / `INVALIDATED` (no further moves). Realized P&L is
**ledger-based**: every trim/close appends an immutable history row carrying
`realized_pnl`, and the outcome sums them — partial exits never mutate the entry
price. Registration is **idempotent on `origin.fingerprint`**, so re-ingesting
the same run never duplicates. Thesis types: `dividend_income`,
`growth_momentum`, `long_term_value`, `special_situation`.

## CLI (`stock-analysis thesis <action>`)

`--state-dir` defaults to `data/theses/`; it may appear after any action.

| Action | What it does |
|---|---|
| `ingest --from-latest` (or `--from-run <dir>`) | Register IDEA theses from a run's `signal_matrix.xlsx`. `--actions Buy,Hold` (default `Buy`), `--type <type>`. |
| `register --ticker T --type <type> --statement "…"` | Manual idea. Optional `--target-price --stop --target-profit --entry-date --shares --review-days`. |
| `list [--ticker --status --type]` | List theses (one line each). |
| `show <id>` | Print one thesis as JSON. |
| `transition <id> ENTRY_READY --reason "…"` | Forward status move (validate the setup). |
| `open <id> --price P --date D [--shares N]` | ENTRY_READY → ACTIVE (record the fill). |
| `trim <id> --shares N --price P --date D` | Partial close (→ PARTIALLY_CLOSED, or CLOSED at zero). |
| `close <id> --reason target_hit --price P --date D` | Close the full remaining position. |
| `invalidate <id> --reason "…" [--price P --date D]` | Kill a thesis (→ INVALIDATED); a price captures P&L. |
| `review-due [--as-of D]` | Theses whose `next_review_date` ≤ as-of. |
| `reviewed <id> --date D [--outcome OK\|WARN\|REVIEW --notes "…"]` | Record a review, advance the next date. |
| `postmortem <id> [--no-prices]` | Markdown report → `data/theses/journal/pm_<id>.md`. Pulls MAE/MFE from yfinance unless `--no-prices`. |
| `summary` | Aggregate realized performance (win rate, avg P&L %, by type). |
| `doctor` / `reindex` | Validate state vs index / rebuild `_index.json`. |

### Typical flow

```bash
stock-analysis run                       # produce output/<ts>/signal_matrix.xlsx
stock-analysis thesis ingest --from-latest          # Buys → IDEA theses
stock-analysis thesis transition <id> ENTRY_READY --reason "base holds"
stock-analysis thesis open <id> --price 198 --date 2026-06-02 --shares 10
# … later …
stock-analysis thesis review-due                    # what needs a look
stock-analysis thesis close <id> --reason target_hit --price 230 --date 2026-06-29
stock-analysis thesis postmortem <id>               # report + MAE/MFE
stock-analysis thesis summary
```

## Library API

```python
from stockanalysis.thesis import (
    register, get, query, transition, open_position, trim, close, terminate,
    mark_reviewed, list_active, list_review_due,
    from_signal_matrix, from_manual, generate_postmortem, summary_stats,
)
from stockanalysis import config, pipeline

state = config.DEFAULT_THESES_DIR
ids = from_signal_matrix(state, pipeline.run().signal_matrix)   # Buys → IDEA theses
```

Every function takes the state dir as its first argument; the lifecycle
functions enforce the state machine, append history, validate, and persist
atomically. See the module map and the "Thesis lifecycle is forward-only and
ledger-based" convention in `CLAUDE.md`. Demo: `notebooks/thesis_tracking.ipynb`.
