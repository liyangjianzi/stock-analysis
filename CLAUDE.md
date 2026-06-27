# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **structured Python package**, `stockanalysis` (under `src/`), implementing an end-to-end equity analysis pipeline for North American markets (NYSE, NASDAQ, TSX): a fundamental screener, technical-indicator computation, interactive Plotly dashboards, and a fused Buy/Hold/Watch signal engine. It is **library-first** — the core is importable with no notebook/IO side effects so a CLI drives it today and a server can later — plus a `stock-analysis` CLI and a thin demo notebook (`notebooks/stock_analysis.ipynb`).

This was converted from a single Jupyter notebook; the logic was ported function-for-function into modules. (The original `stock_analysis.ipynb` may still sit at the project root pending removal.)

## Running

```bash
python -m venv venv && source venv/bin/activate
pip install -e .                 # core; pip install -e ".[gsheets]" adds Google Sheets
stock-analysis run --target excel --out output/    # or: python -m stockanalysis run ...
```

Requires **live network access** (Yahoo Finance via `yfinance`); offline runs fetch nothing and tables/charts come out empty (by design — every fetch degrades gracefully rather than crashing). Each run writes into a fresh timestamped subfolder `output/<YYYY-MM-DD_HHMMSS>/` (so runs don't overwrite each other): `output/<timestamp>/signal_matrix.xlsx` + per-ticker `output/<timestamp>/<TICKER>.html` dashboards. The subdir is resolved once per run by `pipeline.run_output_dir()`.

Library entry point:
```python
from stockanalysis import run
results = run(export_target="excel", save_charts=True)   # -> Results dataclass
```

### Sanity-checking edits without network
- **Test suite (fully offline):** `pip install -e ".[test]"` then `pytest`. The
  `tests/` dir covers the pure-logic surface (screener, indicators, signals,
  `fetch_fundamentals` normalization, `load_watchlist_csv`, the Excel exporter)
  with synthetic OHLCV fixtures — no network / yfinance calls.
- Import check: `PYTHONPATH=src python3 -c "import stockanalysis"`
- Pure-logic functions (`screen_fundamentals`, `add_indicators`, `fit_regression_channel`, `find_support_resistance`, `compute_technical_posture`, `generate_signals`) depend only on pandas/numpy/ta and can be exercised against synthetic OHLCV DataFrames. The Excel exporter and chart builders also run fully offline.

## Architecture

Module map (one responsibility each; core modules never import IPython/`display`):

| Module | Responsibility |
|---|---|
| `config.py` | `load_watchlist_csv()` reads the ticker→sector watchlist from `data/watchlist.csv` (TSX uses `.TO`; default path `DEFAULT_WATCHLIST_CSV`); `HISTORY_PERIOD`, Stage-0 universe/indices |
| `ingest.py` | `fetch_stock_data`, `fetch_fundamentals`, `fetch_profile`, `load_watchlist()` driver |
| `screener.py` | `screen_fundamentals` (0–6) |
| `indicators.py` | `add_indicators` + `fit_regression_channel` + `find_support_resistance` |
| `signals.py` | `compute_technical_posture` (registry-driven 0–N, default 7) + `generate_signals` |
| `overview.py` | Stage-0 daily market overview — **data only**, returns dicts |
| `profile.py` | `build_profile` — deep fundamental report (returns a dict incl. a `report` string) |
| `charts.py` | `build_technical_dashboard`/`build_index_overview` → Plotly `Figure`; `save_html` |
| `pipeline.py` | `Results` dataclass + `run()` orchestrator — **the server-callable API** |
| `cli.py` | `stock-analysis` console script |
| `outputs/` | `Exporter` ABC + `ExcelExporter` + `GSheetsExporter`; `get_exporter(target)` factory |

Data flow (in `pipeline.run`): `load_watchlist` → `prices` + `fundamentals_df` → `screen_fundamentals` → `screened_df` → `add_indicators` (per ticker) → `tech` → `generate_signals` → `signal_matrix` → exporter / chart HTML.

Presentation (pandas `Styler`, `fig.show()`, printing `profile["report"]`) lives **only** in the notebook/CLI, never in the package core.

## Conventions that are easy to get wrong

- **NaN means fail, never crash.** `fetch_fundamentals` returns `np.nan` for any missing field, and `screen_fundamentals` relies on the fact that comparisons against NaN yield `False` — do not "fix" this by dropping NaN rows or it changes scoring semantics.
- **Unit normalization in `fetch_fundamentals`.** yfinance reports `debtToEquity` as a percent (divided by 100 → ratio) and `dividendYield` is defensively divided by 100 when it looks like a percent (>1). Growth fields are already fractional. Thresholds in `screen_fundamentals` assume these normalized units (e.g. `div_yield_min=0.015` = 1.5%).
- **Two independent scores.** Fundamental score is **0–6** (one point per threshold passed). The technical score is **registry-driven**: each predicate in `signals.TECHNICAL_COMPONENTS` is worth +1, so the score is **0–len(components)** (default **7**: price>EMA50, RSI 35–70, recent bullish MACD crossover, positive close-regression slope, **rising EMA50** (positive EMA50-regression slope), volume confirmation, **price near the lower EMA envelope** (bottom 25% of the band — a mean-reversion entry)). `generate_signals` fuses them as `0.70*(fund/6) + 0.30*(tech/len(components))` → Buy ≥0.60, Hold ≥0.40, else Watch. Posture **auto-scales** with the component count: Bearish at 0, Bullish at `score ≥ ceil(⅔·max)` (≥5 of 7), else Neutral. **Add or remove a component by editing the `TECHNICAL_COMPONENTS` list** — the max, composite divisor, posture cutoffs and `detail` keys all derive from it, so no divisor edits are needed; pass `components=` to `compute_technical_posture` to override per call.
- **Indicator column names are a contract.** `add_indicators` writes `EMA20/EMA50/EMA200`, `ENV_UP/ENV_DOWN` (a **data-driven asymmetric** band around EMA20: lower/upper edges at the 2.5th/97.5th percentile of `Close/EMA20−1` so ~`envelope_coverage`=95% of closes fall inside; symmetric ±`envelope_fallback_pct` on <20-bar series), `MACD/MACD_SIG/MACD_HIST`, `RSI`, and the volume columns `VOL_SMA20` (20-day avg volume) + `OBV` (On-Balance Volume). `charts.build_technical_dashboard` and `compute_technical_posture` read these exact names. Trend channels and support/resistance are **not** stored columns — they are window-dependent overlays computed on demand by `fit_regression_channel(close)` (regression mid ±2σ, returns `slope`) and `find_support_resistance(df)` (swing-pivot clustering → horizontal levels), reused by both the dashboard and the posture engine.
- **Exporters are pluggable.** Add an output destination by subclassing `outputs.base.Exporter` and registering it in `outputs.get_exporter`. Keep `gspread`/`google-auth` lazily imported (optional `gsheets` extra) so Excel works without them.
