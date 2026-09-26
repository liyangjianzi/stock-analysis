# StockAnalysis

End-to-end **North American equity analysis** (NYSE, NASDAQ, TSX): a fundamental
screener, technical-indicator engine, interactive Plotly dashboards, and a fused
**Buy / Hold / Watch** signal matrix — packaged as an importable library with a
CLI and a thin demo notebook.

The core is **side-effect-free and importable**, so it can be driven by the CLI
today and a web/server app later. Results export through a **pluggable output
layer** (Excel out of the box; Google Sheets with credentials).

## Install

```bash
python -m venv venv && source venv/bin/activate
pip install -e .                 # core (Excel export + charts)
pip install -e ".[gsheets]"      # add the Google Sheets exporter
pip install -e ".[test]"         # add the test runner (pytest)
pip install -e ".[notebook]"     # ipykernel + nbformat + matplotlib + jinja2 to run the demo notebook
```

## Testing

```bash
pip install -e ".[test]"
pytest
```

The suite under `tests/` (~275 tests, ~15 s) is **fully offline** — synthetic
OHLCV fixtures and monkeypatched fetches, so no network or `yfinance` access is
needed. It covers the screener, indicators, signal engine and trade plans,
fundamentals normalization, the watchlist loader, the exporters and HTML
reports, the pipeline and CLI, the backtest (plan exits, robustness statistics,
random-entry null), the research price cache, and the `thesis/` subpackage.

## Run (CLI)

```bash
stock-analysis run --target excel --out output/
# or:  python -m stockanalysis run --target excel --out output/
```

Outputs land in `output/<timestamp>/`:
- `signal_matrix.xlsx` — *Signal Matrix* + *Fundamentals* sheets
- `report.html` — one combined report: Fundamental Screener + Combined Signal
  Matrix (full) + Trade Plan table + top-N Technical Dashboards + top-N
  Fundamental Profiles + the Daily Market Overview chart

Useful flags: `--period 5y` (default 3y), `--watchlist <csv>`, `--no-report`
(skip the combined report), `--top 5` (dashboards/profiles for the 5 strongest
names only — this is the default), `--fund-min N` (quality cutoff for Buy/Hold,
default 4), `-v` (verbose), `--target none` (compute only, no export).

Position sizing: `--account USD`, `--risk-pct PCT`, `--max-weight PCT` — the CLI
takes **percents** (`--risk-pct 1.0` = 1%); the library API takes fractions. The
default account is a notional $100k.

`scripts/run_daily.sh` wraps a daily run for cron/launchd: the pipeline, then a
non-fatal top-up of the research price cache (below).

## Backtest / signal validation

Does the entry signal actually pay? Two exit models:

```bash
stock-analysis backtest --exits plan --universe data/universe_sp500.csv   # what you'd have traded
stock-analysis backtest --scope technical --period 5y                     # fixed-horizon forward returns
stock-analysis backtest --scope composite                                 # lookahead-caveated
stock-analysis backtest --slippage-mult 2.0                               # stress execution costs
```

**`--exits plan` is the one to trust.** It walks every gate entry to its own
trade-plan stop or target, point-in-time, and reports **R-multiples** — and it
never prints a bare expectancy. Every run also reports:
- the 95% CI **clustered by entry month** (thousands of correlated entries are
  ~120 independent draws, not thousands);
- both halves either side of `--split YYYY-MM-DD` (default: the calendar
  midpoint), plus how many years were positive;
- the **edge over a random entry** — the same plan and exits walked from random
  bars of the same tickers (`--null-reps N`, default 1; 0 skips it). The stop/
  target geometry is profitable on its own, so the bar to beat is the random
  entry, not zero.

Fixed-horizon returns (`--exits horizon`, the default) ignore the stop, so they
flatter the signal; treat them as descriptive only.

**Measured result** (gate entries, plan exits, 503 S&P 500 names, 10 years):
~7,260 trades, **+0.030R, 95% CI [−0.039, +0.098]** — not distinguishable from
zero, nor from a random entry (edge −0.05R, CI [−0.14, +0.04]). Positive
2016–21, negative 2022–26. Before changing any threshold, read
[`.claude/skills/tuning-signals/SKILL.md`](.claude/skills/tuning-signals/SKILL.md):
it records the variants already tested and the hold-out discipline required.

Outputs land in `output/backtest/<timestamp>/`:
- `backtest.xlsx` — *Backtest Summary* + *Event Study*, plus *Planned Trades*,
  *Robustness* and *Yearly R* for `--exits plan`
- `backtest_report.html` — equity curve vs SPY + hit-rate by horizon

**Scopes.** `technical` replays only the price/volume technical posture — it is
recomputed point-in-time (each date sees only past bars), so it is free of
lookahead bias. `composite` replays the full Buy/Hold/Watch decision (quality
cutoff + entry gate), but yfinance exposes only *today's* fundamentals, so past
decisions apply current financials to past prices — **lookahead-biased**, useful
only as a sanity check. Composite output is stamped with that warning.

### Broad-universe research cache

Backtesting a 21-name watchlist measures the names you picked. For a broad
universe, fetch bars once into a local SQLite cache and backtest offline:

```bash
stock-analysis universe --out data/universe_sp500.csv    # 503 current S&P 500 names (committed)
stock-analysis cache --universe data/universe_sp500.csv  # first run: 10y, ~1 min, ~1.2M bars
stock-analysis cache --universe data/universe_sp500.csv  # afterwards: incremental top-up, seconds
stock-analysis cache --status                            # coverage per ticker
```

The cache (`data/cache/prices.db`, gitignored) holds bars on the same
split/dividend-adjusted basis as the live pipeline. Top-ups are incremental; a
ticker that a dividend or split has re-adjusted since the last refresh is
refetched whole rather than stitched onto the old prices. `--full` rebuilds
everything (use it if Yahoo restates history). The universe is **current
constituents only**, so results still carry survivorship bias.

Library entry points: `from stockanalysis import run_backtest`;
`stockanalysis.cache.refresh` / `load_universe`.

## Thesis tracking (trading journal)

Records *why* you bought, when to review, and how it turned out — closing the
**Plan → Trade → Record → Review → Improve** loop the signal matrix alone can't.
Ideas come from a pipeline run's Buys (or by hand), then move through a
forward-only lifecycle to a P&L postmortem. JSON-per-thesis under `data/theses/`;
MAE/MFE uses the same `yfinance` source, so **no extra API key**.

```bash
stock-analysis thesis ingest --from-latest                     # Buys → IDEA theses
stock-analysis thesis open  <id> --price 198 --date 2026-06-02 --shares 10
stock-analysis thesis close <id> --reason target_hit --price 230 --date 2026-06-29
stock-analysis thesis postmortem <id>                          # report + MAE/MFE
stock-analysis thesis summary                                  # win rate, avg P&L %
stock-analysis thesis report                                   # HTML journal of every thesis
```

Lifecycle: `IDEA → ENTRY_READY → ACTIVE → PARTIALLY_CLOSED → CLOSED` (+
`INVALIDATED`). Full command reference and the library API live in
[`src/stockanalysis/thesis/README.md`](src/stockanalysis/thesis/README.md);
library entry point: `from stockanalysis.thesis import register, from_signal_matrix`.

## Google Sheets export

1. Create a Google Cloud **service account** and download its JSON key.
2. `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json`
3. **Share** the target spreadsheet with the service-account email (Editor).
4. Run:
   ```bash
   stock-analysis run --target gsheets --spreadsheet "<sheet id or name>"
   ```
   (or set `$GSHEET_ID` / `$GSHEET_NAME` instead of `--spreadsheet`)

Without credentials the exporter fails with a clear message; Excel still works.

## Use as a library

```python
from stockanalysis import run

results = run(export_target="excel")   # Results dataclass; report.html on by default
results.signal_matrix     # tidy Buy/Hold/Watch DataFrame (pre-ranked: best first)
results.screened_df       # fundamental scores (0–6)
results.tech              # ticker -> indicator-enriched OHLCV DataFrame
results.report_path       # saved combined report.html
results.run_dir           # this run's timestamped output folder

# Skip the combined report, or change how many names its dashboards/profiles cover:
run(save_report=False)
run(top_n=10)              # default is 5

# Or call the building blocks directly (what the future server will do):
import stockanalysis as sa
fig = sa.charts.build_technical_dashboard("MSFT", results.tech)   # plotly Figure
profile = sa.profile.build_profile("AAPL", results.screened_df)["report"]
```

## Project layout

```
data/
  watchlist.csv         ticker,sector watchlist (edit this — loaded at runtime)
  universe_sp500.csv    503-name research universe for broad backtests
  cache/prices.db       research price cache (gitignored, rebuildable)
  theses/               thesis journal state (JSON per thesis)
src/stockanalysis/
  config.py       watchlist loader (load_watchlist_csv), history period, overview universe/indices
  ingest.py       yfinance fetch + load_watchlist() driver
  screener.py     screen_fundamentals (0–6)
  indicators.py   add_indicators + regression channel + support/resistance
  signals.py      compute_technical_posture (registry-driven, default 0–5) + decide_action + generate_signals
  tradeplan.py    build_trade_plan — stop / target / R:R / shares per ticker
  backtest.py     point-in-time replay, plan exits (R-multiples), random-entry null, portfolio sim
  robustness.py   month-clustered CIs, halves / yearly split, edge over the null
  cache.py        SQLite research price cache (refresh / load_universe)
  overview.py     Stage-0 daily market overview (data only)
  profile.py      build_profile (deep fundamental report)
  charts.py       build_* Plotly figures + save_html
  report.py       combined report.html (screener + signals + dashboards + profiles + overview)
  pipeline.py     Results + run() orchestrator  ← server-callable API
  cli.py          `stock-analysis` entry point
  outputs/        Exporter interface + Excel + Google Sheets
  thesis/         thesis tracking (lifecycle + JSON store + postmortems) — see its README
notebooks/
  stock_analysis.ipynb     thin interactive demo over the package
  thesis_tracking.ipynb    thesis lifecycle + postmortem demo
scripts/run_daily.sh       daily job: pipeline + research-cache top-up
research/                  offline harness behind the 2026-09-16 tuning study — see its README
```

## Scoring model

- **Fundamental score (0–6):** one point per threshold passed — P/E, EPS growth,
  revenue growth, debt/equity, dividend yield, positive free cash flow.
- **Technical score (registry-driven, default 0–5):** a pullback/reversal
  pattern — `trend_up` (price above a rising EMA50, +2% over 20 bars),
  `dip_deep` (yesterday's 3-day RSI dipped below 25), `pullback_zone` (price
  within one ATR of EMA50), `turn_confirm` (today closes above yesterday's high
  and above the open), `vol_pattern` (a quiet spell then a ≥1.2× volume
  pickup). Components live in `TECHNICAL_COMPONENTS`; the max follows its
  length. Posture is Bullish at `score ≥ ⌈0.55·max⌉` (≥3 of 5), Bearish when
  nothing fires, else Neutral — cutoffs calibrated to the score's measured
  distribution, which piles up at 1–2 because the components form one pattern.
- **Composite:** `0.70·(fund/6) + 0.30·(tech/N)` (N = len(TECHNICAL_COMPONENTS),
  default 5). The composite **ranks** the matrix; it no longer decides the action.
  The action comes from `signals.decide_action`: **Buy** = fundamental score ≥
  `--fund-min` (default 4) **and** every component in `signals.GATE_COMPONENTS`
  (`trend_up`, `pullback_zone`, `turn_confirm`) fires; **Hold** = quality without
  the entry gate; **Watch** = below `--fund-min`. Every non-Watch row also carries
  a trade plan (entry / stop / target / R:R / shares) — see `tradeplan.py`.

A strong company in a poor tape lands in *Hold/Watch*; a fundamentally weak name
never reaches *Buy* on technicals alone.

> Network required: every run fetches live data from Yahoo Finance via
> `yfinance`. Missing fields degrade gracefully (NaN/skip) rather than crashing.
