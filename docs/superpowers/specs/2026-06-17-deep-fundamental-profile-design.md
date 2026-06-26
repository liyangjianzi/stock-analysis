# Deep Fundamental Profile — Design Spec
**Date:** 2026-06-17
**Project:** StockAnalysis (`stock_analysis.ipynb`)

---

## Context

The existing Step 2 Fundamental Screener scores each stock 0–6 on six pass/fail thresholds (P/E, EPS growth, revenue growth, debt/equity, dividend yield, FCF). This is useful for ranking but provides no qualitative depth: it says nothing about business quality, competitive advantages, management alignment, or how richly valued the growth is.

This feature adds **Step 2b · Deep Fundamental Profile** — a companion `analyze_ticker()` function that produces a rich 5-section per-ticker report covering business overview, extended financial ratios, management quality proxies, moat heuristics, and long-term potential. The existing 6-point score and signal pipeline are **unchanged**.

---

## Pipeline Position

```
Step 2:    screen_fundamentals()    →  screened_df (unchanged)
Step 2b:   analyze_ticker(ticker)  →  printed report (new, standalone)
Step 3–4:  indicators, signals     →  unchanged
```

`analyze_ticker` is called on demand after the screener runs. It does not produce a global or feed any downstream function.

---

## New Notebook Cells

Three new cells inserted after the existing screener display cell (current cell [11]):

```
[Markdown cell]  --- / ## Step 2b · Deep Fundamental Profile
[Code cell]      fetch_profile() + analyze_ticker() definitions
[Code cell]      analyze_ticker("AAPL", screened_df)   # demo
```

---

## New Functions

### `fetch_profile(ticker: str) -> dict`

Calls `yf.Ticker(ticker).info` fresh and extracts ~20 additional fields not present in `fetch_fundamentals`. Returns a flat dict; all values are floats or strings — never raises. Missing values → `np.nan` for numeric, `""` for strings, using the existing `_safe()` helper.

**Fields extracted:**

| Category | yfinance key | Unit | Notes |
|---|---|---|---|
| **Overview** | `longBusinessSummary` | str | Truncated to 400 chars in display |
| | `sector` | str | |
| | `industry` | str | |
| | `country` | str | |
| | `numberOfEmployees` | int | |
| **Profitability** | `grossMargins` | fractional | 0.445 = 44.5% |
| | `operatingMargins` | fractional | |
| | `profitMargins` | fractional | net margin |
| | `returnOnAssets` | fractional | |
| | `returnOnEquity` | fractional | |
| **Growth (fallback)** | `earningsGrowth` | fractional | Used when `screened_df` not passed |
| | `revenueGrowth` | fractional | Used when `screened_df` not passed |
| **Health** | `currentRatio` | ratio | |
| | `quickRatio` | ratio | |
| | `totalCash` | absolute $ | |
| **Valuation** | `priceToBook` | ratio | |
| | `priceToSalesTrailing12Months` | ratio | |
| | `pegRatio` | ratio | NaN if unavailable |
| | `enterpriseToEbitda` | ratio | |
| **Management proxies** | `heldPercentInsiders` | fractional | |
| | `heldPercentInstitutions` | fractional | |
| | `shortPercentOfFloat` | fractional | |

**Unit normalization:** All margin/return/ownership fields from yfinance are already fractional (0.10 = 10%). No division needed. `totalCash` is an absolute dollar value — formatted for display only.

**Error handling:** Entire call wrapped in `try/except`; on failure, returns a dict of NaN/empty values so `analyze_ticker` degrades gracefully.

---

### `analyze_ticker(ticker: str, screened_df: pd.DataFrame = None) -> None`

Calls `fetch_profile(ticker)`, then prints a 5-section formatted report to stdout. If `screened_df` is passed, pulls `EPS_Growth`, `Rev_Growth`, `FCF`, `Fundamental_Score`, and `Price` from it to avoid redundant fetching.

**Section 1 — Business Overview**
Prints: company name (try `shortName` first, fall back to `longName`, then ticker string), sector, industry, country, employee count, and a truncated `longBusinessSummary` (~400 chars). If `screened_df` available, also shows `Fundamental_Score N/6`.

**Section 2 — Financial Ratios**
Prints a 3-column inline table:

- *Profitability:* Gross Margin, Operating Margin, Net Margin, ROE, ROA — formatted as percentages
- *Growth:* Revenue Growth, EPS Growth (from `screened_df` if available, else `fetch_profile`) — formatted as percentages
- *Health:* Current Ratio, Quick Ratio, Debt/Equity (from `screened_df`), Total Cash — formatted as ratios and `$XB`
- *Valuation (inline):* P/E (from `screened_df`), P/B, P/S, PEG, EV/EBITDA — formatted as ratios

NaN values print as `—` (em dash), matching the existing `_fmt_screen` convention.

**Section 3 — Management Quality**

Three binary checks producing a **Management Score (0–3)**:

| Check | Threshold | Label on pass |
|---|---|---|
| Insider ownership | > 5% | "Strong alignment" |
| Institutional ownership | > 50% | "High confidence" |
| Short interest (float) | < 5% | "Low bearish pressure" |

Score label: 3 = Strong, 2 = Neutral, 1 = Weak, 0 = Concern.

*Large-cap note:* When `numberOfEmployees > 50,000`, insider < 5% prints "Low (typical for mega-cap)" instead of failing silently — the threshold still counts as 0 points but is labeled contextually.

**Section 4 — Competitive Advantages (Moat)**

Three quantitative heuristics producing a **Moat Score (0–3)**:

| Check | Threshold | Label on pass |
|---|---|---|
| ROE | > 15% | "Exceptional" |
| Gross Margin | > 30% | "Strong pricing power" |
| Net Margin | > 10% | "High profitability" |

Score label: 3 = Wide Moat, 2 = Narrow Moat, 1 = Uncertain, 0 = Weak/None.

**Section 5 — Long-Term Potential**

Four checks producing a **Long-Term Score (0–4)**:

| Check | Threshold | Signal |
|---|---|---|
| PEG Ratio | < 1.5 | Growth undervalued |
| Revenue Growth | > 10% | Strong top-line momentum |
| EPS Growth | > 15% | Strong earnings power |
| FCF | > 0 | Positive cash generation |

Score label: 4 = Exceptional, 3 = Strong, 2 = Moderate, 1 = Speculative, 0 = Caution.

---

## Output Example

```
══════════════════════════════════════════════════════════════════════
  APPLE INC (AAPL) · Technology · Consumer Electronics
  Employees: 164,000  ·  Country: United States
  Fundamental Score: 5/6  [Strong]
══════════════════════════════════════════════════════════════════════

SECTION 1 · BUSINESS OVERVIEW
  Apple Inc. designs, manufactures, and markets smartphones, personal
  computers, tablets, wearables, and accessories worldwide...

----------------------------------------------------------------------
SECTION 2 · FINANCIAL RATIOS
  Profitability        │  Growth               │  Health
  Gross Margin  44.5%  │  Revenue Growth  7.8%  │  Current Ratio  0.99
  Op. Margin    30.7%  │  EPS Growth     12.4%  │  Quick Ratio    0.96
  Net Margin    25.6%  │                        │  Debt/Equity    1.87
  ROE           157%   │  Valuation             │  Total Cash    $65B
  ROA            29%   │  P/E  29.1  PEG   2.1  │
                       │  P/B  47.2  P/S   7.8  │
                       │  EV/EBITDA      23.6   │

----------------------------------------------------------------------
SECTION 3 · MANAGEMENT QUALITY (proxies)
  Insider Ownership:         0.02%  →  Low (typical for mega-cap)
  Institutional Ownership:  61.4%  →  High confidence
  Short Interest (Float):    0.8%  →  Low bearish pressure
  Management Score: 2/3  [Neutral]

----------------------------------------------------------------------
SECTION 4 · COMPETITIVE ADVANTAGES (moat heuristics)
  ROE           157%   ✓  Exceptional (>15%)
  Gross Margin  44.5%  ✓  Strong pricing power (>30%)
  Net Margin    25.6%  ✓  High profitability (>10%)
  Moat Score: 3/3  [Wide Moat]

----------------------------------------------------------------------
SECTION 5 · LONG-TERM POTENTIAL
  PEG Ratio:        2.1  →  Growth priced in (< 1.5 = undervalued)
  Revenue Growth:  7.8%  →  Moderate (< 10%)
  EPS Growth:     12.4%  →  Moderate (< 15%)
  FCF:             $94B  →  ✓ Positive cash generation
  Long-Term Score: 1/4  [Speculative]
══════════════════════════════════════════════════════════════════════
```

---

## Conventions Respected

- **NaN = fail, never crash:** All yfinance calls wrapped in `try/except`; missing fields → `np.nan` via `_safe()`; NaN values display as `—`
- **Reuses `_safe(info, key)`:** Defined in existing cell [6]; `analyze_ticker` calls it after fetching info
- **No new dependencies:** `yfinance`, `pandas`, `numpy` only — already imported in cell [3]
- **Does not modify `screened_df` or signal pipeline:** Purely additive; no existing cells changed
- **Score ranges kept separate:** Moat (0–3), Management (0–3), Long-Term (0–4) are display-only; they do not feed Fundamental_Score (0–6) or the composite signal

---

## Verification

1. Run `analyze_ticker("AAPL")` with no `screened_df` — all 5 sections should print; Growth section uses fetched data; no crash on missing fields
2. Run `analyze_ticker("AAPL", screened_df)` after Step 2 — Fundamental Score appears in header; Growth section uses cached values from `screened_df`
3. Run on a ticker with minimal data (e.g., a small TSX stock) — all missing fields display as `—`; no exception raised
4. Syntax check: `python3 -c "import json; nb=json.load(open('stock_analysis.ipynb')); [compile(''.join(c['source']),'c','exec') for c in nb['cells'] if c['cell_type']=='code' and not ''.join(c['source']).lstrip().startswith('!')]; print('ok')"`
