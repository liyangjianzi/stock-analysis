# Daily Market Overview — Design Spec
**Date:** 2026-06-16  
**Project:** StockAnalysis (`stock_analysis.ipynb`)

---

## Context

The notebook currently runs as a fixed-watchlist pipeline: a hardcoded `WATCHLIST` of 15 tickers is analyzed end-to-end each session. There is no mechanism for discovering timely opportunities or assessing broader market conditions before running the analysis.

This feature adds a **Stage 0** — a Daily Market Overview that runs *before* ingestion. It checks major indices, surfaces stocks with high news momentum and upcoming earnings, and dynamically enriches the `WATCHLIST` for the current session. The goal is a 10-minute morning check that makes the pipeline market-aware.

---

## Pipeline Position

```
Stage 0 (NEW): Daily Market Overview  →  expands WATCHLIST in-memory
Stage 1:        Ingestion             →  unchanged, fetches expanded WATCHLIST
Stage 2:        Screener              →  unchanged
Stage 3:        Indicators            →  unchanged
Stage 4:        Signals + Export      →  unchanged
```

Stage 0 does **not** modify the static `WATCHLIST` definition in the notebook — it calls `WATCHLIST.update(...)` in-memory so each session starts fresh.

---

## New Globals

### `CANDIDATE_UNIVERSE`
A hardcoded `dict[str, str]` of ~30 stocks (ticker → sector) spanning S&P 500, NASDAQ, and TSX blue-chips. Serves as the discovery pool. Selected to cover diverse sectors beyond the core watchlist.

```python
CANDIDATE_UNIVERSE = {
    # Technology
    "NVDA": "Technology", "META": "Technology", "GOOGL": "Technology",
    "AMD": "Technology", "CRM": "Technology",
    # Financials
    "GS": "Financials", "MS": "Financials", "BAC": "Financials",
    "BLK": "Financials", "RY.TO": "Financials",
    # Healthcare
    "UNH": "Healthcare", "LLY": "Healthcare", "ABBV": "Healthcare",
    "MRK": "Healthcare", "ABT": "Healthcare",
    # Consumer
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "MCD": "Consumer Staples",
    "PG": "Consumer Staples",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "SU.TO": "Energy",
    # Industrials
    "CAT": "Industrials", "BA": "Industrials", "UNP": "Industrials",
    # Utilities / Real Estate
    "NEE": "Utilities", "AMT": "Real Estate",
    # Materials
    "LIN": "Materials", "NEM": "Materials",
}
```

### `OVERVIEW_INDICES`
```python
OVERVIEW_INDICES = {"S&P 500": "^GSPC", "NASDAQ": "^IXIC", "TSX": "^GSPTSE"}
VIX_TICKER = "^VIX"
OVERVIEW_LOOKBACK = 60  # trading days for charts
```

---

## New Functions

### `scan_candidates(universe, news_hours=48, earnings_days=14, top_n=5)`

Scores each ticker in `universe` on two signals and returns the top N.

**Signals:**

| Signal | Source | Max score |
|---|---|---|
| News mentions | `yfinance.Ticker.news` — count items where `providerPublishTime` is within `news_hours` | 0.60 |
| Earnings proximity | `yfinance.Ticker.calendar` — days until next earnings report | 0.40 |

Earnings score mapping:
- < 7 days away → 1.0
- 7–14 days away → 0.5  
- > 14 days away → 0.0
- No calendar data → 0.0

`discovery_score = 0.60 * normalized_news_count + 0.40 * earnings_score`

News count is normalized: `min(news_count / 5, 1.0)` (5+ articles in 48h = max score).

Returns a DataFrame with columns: `Ticker`, `Sector`, `News_Count`, `Days_To_Earnings`, `Discovery_Score`, sorted descending.

**Error handling:** any ticker that raises during `.news` or `.calendar` is silently skipped (NaN-safe, matches existing notebook pattern).

---

### `plot_index_overview(index_data)`

Plotly figure with 2 panels (follows notebook's `plotly_white`, 850px, shared x-axis, unified hover conventions):

- **Panel 1:** Normalized price history (last 60 days, rebased to 100 at start) for all 3 indices on one trace each. Makes relative performance immediately comparable.
- **Panel 2:** VIX level over the same period, with horizontal reference lines at 15 (low) and 25 (elevated).

`index_data` is a dict: index name → OHLCV DataFrame (produced by `_fetch_index_data()`).

---

### `_fetch_index_data()`

Internal helper. Fetches 60-day OHLCV via `yfinance.download()` for all indices + VIX. Computes per-index:
- Day % change: `(close[-1] - close[-2]) / close[-2]`
- Week % change: `(close[-1] - close[-6]) / close[-6]`  
- YTD % change: fetch Jan 1 of current year; use `.asof(pd.Timestamp(year, 1, 1))` to get the first available trading day's close
- RSI (14): via `ta.momentum.RSIIndicator` (matches existing `add_indicators` usage)

Returns `dict[str, DataFrame]` with keys matching `OVERVIEW_INDICES` names plus `"VIX"` (e.g., `{"S&P 500": df, "NASDAQ": df, "TSX": df, "VIX": df}`). `plot_index_overview` extracts VIX from `index_data["VIX"]` for Panel 2.

---

### `generate_daily_overview(signal_matrix=None, tech=None, prices=None, top_n=5)`

Main entry point. All three pipeline parameters are optional.

**Execution steps:**

1. Call `_fetch_index_data()` — fetch indices + VIX
2. Call `scan_candidates(CANDIDATE_UNIVERSE, top_n=top_n)` — discover tickers
3. Call `plot_index_overview(index_data)` — display chart
4. Print **Section 1: Market Indices** — table of index levels, day/week/YTD % change, RSI, trend (above/below EMA50)
5. Print **Section 2: Risk Gauge** — VIX level with label (Low / Moderate / Elevated / High)
6. Print **Section 3: News Headlines** — top 10 headlines from index tickers + top discovered tickers (deduplicated by URL, sorted by recency)
7. Print **Section 4: Discovery Table** — `scan_candidates` results table with scores and earnings flags
8. Print **Section 5: Action Plan** — if `signal_matrix` passed: Buy/Hold/Watch counts + top 3 Buy names by Composite score; else: top 3 discovered tickers by Discovery_Score (with Bullish posture noted if `tech` is available)
9. **Enrich WATCHLIST:** `WATCHLIST.update({t: s for t, s in top_picks[['Ticker','Sector']].values})`

**Risk thresholds (VIX):**
- < 15 → Low
- 15–25 → Moderate  
- 25–35 → Elevated
- > 35 → High

---

## Notebook Cell Structure

Three new cells added **before** the existing Stage 1 ingestion loop:

```
[Markdown cell]  ## Stage 0: Daily Market Overview
[Code cell]      CANDIDATE_UNIVERSE = {...}
                 OVERVIEW_INDICES = {...}
                 # _fetch_index_data(), scan_candidates(), plot_index_overview(), generate_daily_overview() definitions
[Code cell]      generate_daily_overview()   # run standalone each morning
```

The existing ingestion driver loop cell is unchanged — it already reads from `WATCHLIST`, which will now contain the enriched set.

---

## Conventions Respected

- **NaN = fail, never crash:** All yfinance calls wrapped in try/except, missing data → skip (not error)
- **No new dependencies:** Uses only `yfinance`, `ta`, `pandas`, `numpy`, `plotly` — all already imported
- **Indicator naming contract:** No new indicator columns added to `tech`; Stage 0 does not mutate `tech`
- **Score ranges:** Does not touch Fundamental (0–6), Technical (0–3), or Composite (0–1) scoring

---

## Verification

1. Run `generate_daily_overview()` in isolation (no pipeline globals) — should print all sections and display chart without error
2. Run the full pipeline top-to-bottom — WATCHLIST should contain the 15 core tickers plus up to 5 discovered tickers; signal_matrix should have rows for all of them
3. Run `generate_daily_overview(signal_matrix=signal_matrix, tech=tech)` after Stage 4 — Section 5 action plan should show Buy/Hold/Watch breakdown
4. Syntax check: `python3 -c "import json; nb=json.load(open('stock_analysis.ipynb')); [compile(''.join(c['source']),'c','exec') for c in nb['cells'] if c['cell_type']=='code' and not ''.join(c['source']).lstrip().startswith('!')]; print('ok')"`
