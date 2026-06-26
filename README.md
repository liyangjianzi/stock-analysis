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

Outputs land in `output/`:
- `output/signal_matrix.xlsx` — *Signal Matrix* + *Fundamentals* sheets
- `output/<TICKER>.html` — one interactive technical dashboard per screened ticker

Useful flags: `--period 5y`, `--no-charts`, `-v` (verbose), `--target none`
(compute only, no export).

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

results = run(export_target="excel", save_charts=True)   # Results dataclass
results.signal_matrix     # tidy Buy/Hold/Watch DataFrame
results.screened_df       # fundamental scores (0–6)
results.tech              # ticker -> indicator-enriched OHLCV DataFrame
results.chart_paths       # saved HTML dashboards

# Or call the building blocks directly (what the future server will do):
import stockanalysis as sa
fig = sa.charts.build_technical_dashboard("MSFT", results.tech)   # plotly Figure
report = sa.profile.build_profile("AAPL", results.screened_df)["report"]
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
  signals.py      compute_technical_posture (0–5) + generate_signals
  overview.py     Stage-0 daily market overview (data only)
  profile.py      build_profile (deep fundamental report)
  charts.py       build_* Plotly figures + save_html
  pipeline.py     Results + run() orchestrator  ← server-callable API
  cli.py          `stock-analysis` entry point
  outputs/        Exporter interface + Excel + Google Sheets
notebooks/
  stock_analysis.ipynb   thin interactive demo over the package
```

## Scoring model

- **Fundamental score (0–6):** one point per threshold passed — P/E, EPS growth,
  revenue growth, debt/equity, dividend yield, positive free cash flow.
- **Technical score (0–5):** price > EMA50, RSI in 35–70, recent bullish MACD
  crossover, positive regression-channel slope, volume confirmation.
- **Composite:** `0.70·(fund/6) + 0.30·(tech/5)` → **Buy ≥ 0.60 · Hold ≥ 0.40 ·
  Watch < 0.40**.

A strong company in a poor tape lands in *Hold/Watch*; a fundamentally weak name
never reaches *Buy* on technicals alone.

> Network required: every run fetches live data from Yahoo Finance via
> `yfinance`. Missing fields degrade gracefully (NaN/skip) rather than crashing.
