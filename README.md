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

The suite under `tests/` is **fully offline** — it exercises the pure-logic
surface (screener, indicators, signal engine, fundamentals normalization, the
watchlist CSV loader, and the Excel exporter) against synthetic OHLCV fixtures,
so no network or `yfinance` access is needed.

## Run (CLI)

```bash
stock-analysis run --target excel --out output/
# or:  python -m stockanalysis run --target excel --out output/
```

Outputs land in `output/<timestamp>/`:
- `signal_matrix.xlsx` — *Signal Matrix* + *Fundamentals* sheets
- `report.html` — one combined report: Fundamental Screener + Combined Signal
  Matrix (full) + top-N Technical Dashboards + top-N Fundamental Profiles +
  the Daily Market Overview chart

Useful flags: `--period 5y`, `--no-report` (skip the combined report),
`--top 5` (dashboards/profiles for the 5 strongest names only — this is the
default), `-v` (verbose), `--target none` (compute only, no export).

## Backtest / signal validation

Validate the signals against history (does a Bullish/Buy signal precede
above-baseline returns?):

```bash
stock-analysis backtest --scope technical --period 5y          # honest, price-only
stock-analysis backtest --scope composite                      # lookahead-caveated
stock-analysis backtest --slippage-mult 2.0                    # stress execution costs
```

Outputs land in `output/backtest/<timestamp>/`:
- `backtest.xlsx` — *Backtest Summary* + *Event Study* sheets
- `backtest_report.html` — equity curve vs SPY + hit-rate by horizon

**Scopes.** `technical` replays only the price/volume technical posture — it is
recomputed point-in-time (each date sees only past bars), so it is free of
lookahead bias. `composite` replays the full `0.70·fundamental + 0.30·technical`
Buy/Hold/Watch signal, but yfinance exposes only *today's* fundamentals, so past
composites apply current financials to past prices — **lookahead-biased**, useful
only as a sanity check. Composite output is stamped with that warning.

Library entry point: `from stockanalysis import run_backtest`.

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
  watchlist.csv   ticker,sector watchlist (edit this — loaded at runtime)
src/stockanalysis/
  config.py       watchlist loader (load_watchlist_csv), history period, overview universe/indices
  ingest.py       yfinance fetch + load_watchlist() driver
  screener.py     screen_fundamentals (0–6)
  indicators.py   add_indicators + regression channel + support/resistance
  signals.py      compute_technical_posture (registry-driven, default 0–5) + generate_signals
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
  length, and posture is Bullish at `score ≥ ⌈⅔·max⌉` (≥4 of 5).
- **Composite:** `0.70·(fund/6) + 0.30·(tech/N)` (N = len(TECHNICAL_COMPONENTS),
  default 5) → **Buy ≥ 0.60 · Hold ≥ 0.40 · Watch < 0.40**.

A strong company in a poor tape lands in *Hold/Watch*; a fundamentally weak name
never reaches *Buy* on technicals alone.

> Network required: every run fetches live data from Yahoo Finance via
> `yfinance`. Missing fields degrade gracefully (NaN/skip) rather than crashing.
