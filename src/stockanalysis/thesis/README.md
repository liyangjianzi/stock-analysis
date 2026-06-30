# `stockanalysis.thesis` — investment thesis tracking

A lean, native port of the `trader-memory-core` skill. The signal engine answers
*"what looks good today"*; this subpackage answers *"what did I act on, why, when
do I review it, and how did it turn out"* — closing the **Plan → Trade → Record →
Review → Improve** loop a one-shot Buy/Hold/Watch signal can't.

It is a **separate, on-demand feature** — not part of `pipeline.run`. Storage is
**JSON-per-thesis** under `data/theses/` (stdlib only, no new dependency), and
MAE/MFE is computed from the same `yfinance` source the rest of the package uses,
so it needs **no FMP/paid API key** (unlike the upstream skill).

## Lifecycle (forward-only)

```
IDEA → ENTRY_READY → ACTIVE → PARTIALLY_CLOSED → CLOSED
   └──────────────────(any non-terminal)──────────────→ INVALIDATED
```

`CLOSED` / `INVALIDATED` are terminal. Status changes only through the lifecycle
functions, which enforce the order, append to `status_history`, re-validate, and
write atomically.

## Key contracts

- **Ledger-based P&L.** Every trim/close appends an immutable history row carrying
  `realized_pnl`; the outcome sums them, so partial exits never mutate the entry
  price. All exits funnel through one `store._record_sale` helper, so `trim` /
  `close` / a priced `terminate` produce identically-shaped rows.
- **Idempotent registration.** Theses are deduped on `origin.fingerprint`, so
  re-ingesting the same `signal_matrix` never creates duplicates.
- **JSON-per-thesis + `_index.json`** under `data/theses/`; postmortems land in
  `data/theses/journal/pm_<id>.md`. Validation is plain Python in `model.py` (no
  `jsonschema`).
- **Thesis types:** `dividend_income`, `growth_momentum`, `long_term_value`,
  `special_situation`.

## CLI quickstart

```bash
stock-analysis run                                  # → output/<ts>/signal_matrix.xlsx
stock-analysis thesis ingest --from-latest          # Buys → IDEA theses
stock-analysis thesis transition <id> ENTRY_READY --reason "base holds"
stock-analysis thesis open  <id> --price 198 --date 2026-06-02 --shares 10
stock-analysis thesis trim  <id> --shares 4 --price 220 --date 2026-06-10
stock-analysis thesis close <id> --reason target_hit --price 230 --date 2026-06-29
stock-analysis thesis review-due                    # what needs a look today
stock-analysis thesis postmortem <id>               # markdown report + MAE/MFE
stock-analysis thesis summary                        # win rate, avg P&L %, by type
```

Other actions: `register` (manual idea), `list`, `show`, `invalidate`,
`reviewed`, `doctor`, `reindex`. `--state-dir` defaults to `data/theses/` and may
appear after any action. Use `--no-prices` on `postmortem` to stay fully offline.

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
tid = from_manual(state, {                                       # or a hand-formed idea
    "ticker": "MSFT", "thesis_type": "long_term_value",
    "thesis_statement": "Durable franchise, fair price.",
    "target_price": 380, "stop_price": 340,
})
```

Every function takes the state dir as its first argument.

## Modules

| Module | Responsibility |
|---|---|
| `model.py` | Canonical thesis shape, deterministic id + idempotency fingerprint, datetime helpers, pure-Python invariant validation. |
| `store.py` | JSON persistence (atomic writes) + the lifecycle functions and ledger-based P&L. |
| `sources.py` | `from_signal_matrix` and `from_manual` registration adapters. |
| `review.py` | Injectable `YFinancePriceAdapter`, MAE/MFE, markdown postmortem, summary stats. |
| `cli.py` | The `stock-analysis thesis …` subcommand. |

Demo notebook: [`notebooks/thesis_tracking.ipynb`](../../../notebooks/thesis_tracking.ipynb).
Skill: [`.claude/skills/thesis-tracking/SKILL.md`](../../../.claude/skills/thesis-tracking/SKILL.md).
See the root `CLAUDE.md` for the architecture map and the lifecycle convention notes.
