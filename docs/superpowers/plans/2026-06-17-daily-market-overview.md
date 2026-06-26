# Daily Market Overview — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Stage 0 Daily Market Overview to `stock_analysis.ipynb` that checks major indices, discovers high-momentum stocks via news + earnings calendar, enriches `WATCHLIST` in-memory, and runs before the existing ingestion pipeline.

**Architecture:** Three new Jupyter cells are inserted between cell [6] (fetch helpers) and cell [7] (ingestion loop): a markdown header, a code cell with all Stage 0 globals and function definitions, and a code cell with the `generate_daily_overview()` call. No existing cells are modified. The new `WATCHLIST.update()` call at the end of `generate_daily_overview()` feeds the unchanged ingestion driver.

**Tech Stack:** `yfinance`, `ta` (EMAIndicator, RSIIndicator), `pandas`, `numpy`, `plotly` — all already imported in cell [3].

---

## File Map

| File | Action | What changes |
|---|---|---|
| `stock_analysis.ipynb` | Modify | Insert 3 new cells at position 7 (between cells [6] and [7]) |

---

## Task 1: Insert Stage 0 cells into the notebook

**Files:**
- Modify: `stock_analysis.ipynb` (insert at cell index 7)

The notebook is pure JSON. We use a Python script to load it, splice in the new cells, and write it back. All Stage 0 code lives in one code cell (definitions) + one execution cell.

- [ ] **Step 1: Run the insertion script**

Save and run this Python script (not inside the notebook — run it from the terminal):

```python
import json, copy

NB_PATH = "/Users/liyanglu/PycharmProjects/StockAnalysis/stock_analysis.ipynb"

def make_markdown_cell(source):
    return {"cell_type": "markdown", "metadata": {}, "source": source}

def make_code_cell(source):
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }

MARKDOWN_SOURCE = [
    "---\n",
    "## Stage 0 · Daily Market Overview\n",
    "\n",
    "Runs **before** ingestion. Fetches major index data, scans a candidate universe\n",
    "for high-news-momentum + near-earnings tickers, displays an interactive chart,\n",
    "and enriches `WATCHLIST` in-memory with the top discoveries before the data\n",
    "pipeline runs.\n",
    "\n",
    "Call `generate_daily_overview()` standalone (no arguments) for a quick morning\n",
    "check, or pass pipeline globals after Stage 4 for a richer action plan.\n",
]

DEFS_SOURCE = [
    "# =============================================================================\n",
    "# Stage 0 configuration\n",
    "# =============================================================================\n",
    "CANDIDATE_UNIVERSE = {\n",
    "    # Technology\n",
    '    "META": "Technology", "AMD": "Technology", "CRM": "Technology",\n',
    '    "ORCL": "Technology", "ADBE": "Technology",\n',
    "    # Financials\n",
    '    "GS": "Financials", "MS": "Financials", "BLK": "Financials",\n',
    '    "AXP": "Financials", "V": "Financials",\n',
    "    # Healthcare\n",
    '    "LLY": "Healthcare", "MRK": "Healthcare", "ABT": "Healthcare",\n',
    '    "TMO": "Healthcare", "DHR": "Healthcare",\n',
    "    # Consumer\n",
    '    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",\n',
    '    "NKE": "Consumer Discretionary", "MCD": "Consumer Staples",\n',
    '    "PG": "Consumer Staples",\n',
    "    # Energy\n",
    '    "XOM": "Energy", "CVX": "Energy", "SU.TO": "Energy",\n',
    "    # Industrials\n",
    '    "CAT": "Industrials", "BA": "Industrials", "UNP": "Industrials",\n',
    "    # Utilities / Real Estate / Materials\n",
    '    "NEE": "Utilities", "AMT": "Real Estate", "LIN": "Materials",\n',
    "}\n",
    "\n",
    'OVERVIEW_INDICES = {"S&P 500": "^GSPC", "NASDAQ": "^IXIC", "TSX": "^GSPTSE"}\n',
    'VIX_TICKER = "^VIX"\n',
    "OVERVIEW_LOOKBACK = 60  # trading days for index chart\n",
    "\n",
    "\n",
    "def _fetch_index_data():\n",
    '    """Fetch OVERVIEW_LOOKBACK days of OHLCV for indices + VIX.\n',
    "\n",
    "    Returns dict: name -> DataFrame with keys matching OVERVIEW_INDICES plus 'VIX'.\n",
    '    """\n',
    "    result = {}\n",
    "    fetch_map = {**OVERVIEW_INDICES, \"VIX\": VIX_TICKER}\n",
    "    for name, ticker in fetch_map.items():\n",
    "        try:\n",
    '            df = yf.download(ticker, period="1y", interval="1d",\n',
    "                             auto_adjust=True, progress=False)\n",
    "            if df is None or df.empty:\n",
    "                continue\n",
    "            df.index = pd.to_datetime(df.index).tz_localize(None)\n",
    "            if isinstance(df.columns, pd.MultiIndex):\n",
    "                df.columns = df.columns.get_level_values(0)\n",
    "            result[name] = df.tail(OVERVIEW_LOOKBACK)\n",
    "        except Exception as e:\n",
    '            print(f"  ⚠️  {name} ({ticker}): {e}")\n',
    "    return result\n",
    "\n",
    "\n",
    "def _index_stats(df):\n",
    '    """Compute day/week/YTD % change, RSI, and EMA50 trend for an index DataFrame."""\n',
    '    close = df["Close"].dropna()\n',
    "    if len(close) < 6:\n",
    "        return {}\n",
    "    day_chg = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100\n",
    "    week_chg = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100\n",
    "    year = datetime.now().year\n",
    "    ytd_ref = close.asof(pd.Timestamp(year, 1, 1))\n",
    "    ytd_chg = ((close.iloc[-1] - ytd_ref) / ytd_ref * 100\n",
    "               if pd.notna(ytd_ref) and ytd_ref > 0 else np.nan)\n",
    "    rsi_val = np.nan\n",
    "    if len(close) >= 14:\n",
    "        rsi_val = RSIIndicator(close, window=14).rsi().iloc[-1]\n",
    "    ema50 = np.nan\n",
    "    if len(close) >= 50:\n",
    "        ema50 = EMAIndicator(close, window=50).ema_indicator().iloc[-1]\n",
    '    trend = "Above EMA50" if pd.notna(ema50) and close.iloc[-1] > ema50 else "Below EMA50"\n',
    "    return {\n",
    '        "Last": close.iloc[-1],\n',
    '        "Day %": day_chg,\n',
    '        "Week %": week_chg,\n',
    '        "YTD %": ytd_chg,\n',
    '        "RSI": rsi_val,\n',
    '        "Trend": trend,\n',
    "    }\n",
    "\n",
    "\n",
    "def scan_candidates(universe, news_hours=48, earnings_days=14, top_n=5):\n",
    '    """Score candidate universe on news mentions + earnings proximity.\n',
    "\n",
    "    Tickers already in WATCHLIST are skipped. Returns top_n*2 rows so caller\n",
    "    can pick the top_n after deduplication.\n",
    '    """\n',
    "    from datetime import timezone\n",
    "    cutoff_ts = (datetime.now(timezone.utc) - pd.Timedelta(hours=news_hours)).timestamp()\n",
    "    rows = []\n",
    "    for ticker, sector in universe.items():\n",
    "        if ticker in WATCHLIST:\n",
    "            continue\n",
    "        try:\n",
    "            tk = yf.Ticker(ticker)\n",
    "            news = tk.news or []\n",
    "            recent = sum(\n",
    "                1 for n in news\n",
    '                if n.get("providerPublishTime", 0) >= cutoff_ts\n',
    "            )\n",
    "            days_to_earn = np.nan\n",
    "            try:\n",
    "                cal = tk.calendar\n",
    "                if cal is not None:\n",
    "                    if isinstance(cal, pd.DataFrame) and not cal.empty:\n",
    '                        if "Earnings Date" in cal.index:\n',
    '                            earn_date = pd.to_datetime(cal.loc["Earnings Date"].iloc[0])\n',
    "                            days_to_earn = max(0, (earn_date - pd.Timestamp.now()).days)\n",
    "                    elif isinstance(cal, dict):\n",
    '                        key = next((k for k in cal if "Earnings" in str(k)), None)\n',
    "                        if key and cal[key]:\n",
    "                            earn_date = pd.to_datetime(cal[key][0])\n",
    "                            days_to_earn = max(0, (earn_date - pd.Timestamp.now()).days)\n",
    "            except Exception:\n",
    "                pass\n",
    "            rows.append({\n",
    '                "Ticker": ticker, "Sector": sector,\n',
    '                "News_Count": recent, "Days_To_Earnings": days_to_earn,\n',
    "            })\n",
    "        except Exception:\n",
    "            continue\n",
    "    if not rows:\n",
    '        return pd.DataFrame(columns=["Ticker","Sector","News_Count","Days_To_Earnings","Discovery_Score"])\n',
    "    df = pd.DataFrame(rows)\n",
    '    df["News_Score"] = df["News_Count"].apply(lambda x: min(x / 5.0, 1.0))\n',
    "    def _earn_score(d):\n",
    "        if pd.isna(d) or d < 0:\n",
    "            return 0.0\n",
    "        return 1.0 if d < 7 else (0.5 if d <= earnings_days else 0.0)\n",
    '    df["Earnings_Score"] = df["Days_To_Earnings"].apply(_earn_score)\n',
    '    df["Discovery_Score"] = 0.60 * df["News_Score"] + 0.40 * df["Earnings_Score"]\n',
    '    df = df.drop(columns=["News_Score", "Earnings_Score"])\n',
    '    return df.sort_values("Discovery_Score", ascending=False).reset_index(drop=True).head(top_n * 2)\n',
    "\n",
    "\n",
    "def plot_index_overview(index_data):\n",
    '    """2-panel Plotly chart: normalized index price history + VIX."""\n',
    "    fig = make_subplots(\n",
    "        rows=2, cols=1, shared_xaxes=True,\n",
    "        row_heights=[0.7, 0.3], vertical_spacing=0.04,\n",
    '        subplot_titles=("Major Indices — Relative Performance (rebased to 100)",\n',
    '                        "VIX (Volatility Index)"),\n',
    "    )\n",
    '    _colors = {"S&P 500": "#1f77b4", "NASDAQ": "#ff7f0e", "TSX": "#2ca02c"}\n',
    "    for name, df in index_data.items():\n",
    '        if name == "VIX" or df.empty:\n',
    "            continue\n",
    '        close = df["Close"].dropna()\n',
    "        if close.empty:\n",
    "            continue\n",
    "        rebased = close / close.iloc[0] * 100\n",
    "        fig.add_trace(\n",
    "            go.Scatter(x=rebased.index, y=rebased.values, name=name,\n",
    "                       line=dict(color=_colors.get(name), width=2)),\n",
    "            row=1, col=1,\n",
    "        )\n",
    '    if "VIX" in index_data and not index_data["VIX"].empty:\n',
    '        vix_close = index_data["VIX"]["Close"].dropna()\n',
    "        fig.add_trace(\n",
    '            go.Scatter(x=vix_close.index, y=vix_close.values, name="VIX",\n',
    '                       line=dict(color="#d62728", width=2),\n',
    '                       fill="tozeroy", fillcolor="rgba(214,39,40,0.1)"),\n',
    "            row=2, col=1,\n",
    "        )\n",
    "        for level, label in [(15, \"Low/Moderate\"), (25, \"Moderate/Elevated\")]:\n",
    "            fig.add_hline(y=level, row=2, col=1,\n",
    "                          line=dict(color=\"gray\", dash=\"dash\", width=1),\n",
    "                          annotation_text=label, annotation_position=\"right\")\n",
    "    fig.update_layout(\n",
    "        height=PLOT_HEIGHT, hovermode=\"x unified\",\n",
    "        legend=dict(orientation=\"h\", yanchor=\"bottom\", y=1.02, xanchor=\"right\", x=1),\n",
    "    )\n",
    "    fig.update_xaxes(showspikes=True, spikemode=\"across\")\n",
    "    fig.update_yaxes(showspikes=True)\n",
    "    fig.show()\n",
    "\n",
    "\n",
    "def generate_daily_overview(signal_matrix=None, tech=None, prices=None, top_n=5):\n",
    '    """Stage 0: Daily Market Overview.\n',
    "\n",
    "    Standalone by default (no args needed). Pass signal_matrix and/or tech\n",
    "    after running the full pipeline for an enriched action plan.\n",
    "    Enriches WATCHLIST in-memory with top discovered tickers before returning.\n",
    '    """\n',
    '    SEP = "─" * 70\n',
    '    print("=" * 70)\n',
    "    print(f\"  DAILY MARKET OVERVIEW  ·  {datetime.now():%Y-%m-%d %H:%M}\")\n",
    '    print("=" * 70)\n',
    "\n",
    "    # 1. Fetch index data\n",
    '    print("\\nFetching index data...")\n',
    "    index_data = _fetch_index_data()\n",
    "\n",
    "    # 2. Discover candidates\n",
    '    print("Scanning candidates for news momentum + upcoming earnings...")\n',
    "    candidates_df = scan_candidates(CANDIDATE_UNIVERSE, top_n=top_n)\n",
    "\n",
    "    # 3. Chart\n",
    "    if index_data:\n",
    "        plot_index_overview(index_data)\n",
    "\n",
    "    # --- Section 1: Market Indices ---\n",
    "    print(f\"\\n{SEP}\")\n",
    '    print("  SECTION 1 · MAJOR INDICES")\n',
    "    print(SEP)\n",
    "    stats_rows = []\n",
    "    for name, df in index_data.items():\n",
    '        if name == "VIX":\n',
    "            continue\n",
    "        s = _index_stats(df)\n",
    "        if s:\n",
    '            s["Index"] = name\n',
    "            stats_rows.append(s)\n",
    "    if stats_rows:\n",
    '        idx_df = pd.DataFrame(stats_rows).set_index("Index")\n',
    '        idx_df["Day %"]  = idx_df["Day %"].map(lambda x: f"{x:+.2f}%")\n',
    '        idx_df["Week %"] = idx_df["Week %"].map(lambda x: f"{x:+.2f}%")\n',
    '        idx_df["YTD %"]  = idx_df["YTD %"].map(lambda x: f"{x:+.2f}%" if pd.notna(x) else "N/A")\n',
    '        idx_df["RSI"]    = idx_df["RSI"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "N/A")\n',
    '        idx_df["Last"]   = idx_df["Last"].map(lambda x: f"{x:,.2f}")\n',
    "        print(idx_df.to_string())\n",
    "\n",
    "    # --- Section 2: Risk Gauge ---\n",
    "    print(f\"\\n{SEP}\")\n",
    '    print("  SECTION 2 · RISK GAUGE")\n',
    "    print(SEP)\n",
    '    if "VIX" in index_data and not index_data["VIX"].empty:\n',
    '        vix_now = index_data["VIX"]["Close"].dropna().iloc[-1]\n',
    "        label = (\"LOW\" if vix_now < 15 else\n",
    "                 \"MODERATE\" if vix_now < 25 else\n",
    "                 \"ELEVATED\" if vix_now < 35 else \"HIGH\")\n",
    "        print(f\"  VIX: {vix_now:.2f}  →  Risk Level: {label}\")\n",
    "    else:\n",
    '        print("  VIX: unavailable")\n',
    "\n",
    "    # --- Section 3: News Headlines ---\n",
    "    print(f\"\\n{SEP}\")\n",
    '    print("  SECTION 3 · RECENT NEWS HEADLINES (last 48 h)")\n',
    "    print(SEP)\n",
    "    from datetime import timezone\n",
    "    cutoff_ts = (datetime.now(timezone.utc) - pd.Timedelta(hours=48)).timestamp()\n",
    "    seen_urls, headlines = set(), []\n",
    "    news_tickers = list(OVERVIEW_INDICES.values())\n",
    "    if not candidates_df.empty:\n",
    '        news_tickers += candidates_df["Ticker"].head(5).tolist()\n',
    "    for ticker in news_tickers:\n",
    "        try:\n",
    "            for item in (yf.Ticker(ticker).news or []):\n",
    '                url = item.get("link", "")\n',
    '                ts  = item.get("providerPublishTime", 0)\n',
    "                if url and url not in seen_urls and ts >= cutoff_ts:\n",
    "                    seen_urls.add(url)\n",
    "                    pub = datetime.fromtimestamp(ts).strftime(\"%m-%d %H:%M\")\n",
    '                    headlines.append((ts, pub, item.get("title",""), item.get("publisher","")))\n',
    "        except Exception:\n",
    "            continue\n",
    "    headlines.sort(reverse=True)\n",
    "    for _, pub, title, publisher in headlines[:10]:\n",
    "        print(f\"  [{pub}] {title}  ({publisher})\")\n",
    "    if not headlines:\n",
    '        print("  No recent headlines available.")\n',
    "\n",
    "    # --- Section 4: Discovery Table ---\n",
    "    print(f\"\\n{SEP}\")\n",
    '    print("  SECTION 4 · CANDIDATE DISCOVERY")\n',
    "    print(SEP)\n",
    "    if not candidates_df.empty:\n",
    "        display_df = candidates_df.copy()\n",
    '        display_df["Days_To_Earnings"] = display_df["Days_To_Earnings"].apply(\n',
    '            lambda x: f"{int(x)}d" if pd.notna(x) else "N/A"\n',
    "        )\n",
    '        display_df["Discovery_Score"] = display_df["Discovery_Score"].map("{:.3f}".format)\n',
    "        print(display_df.to_string(index=False))\n",
    "    else:\n",
    '        print("  No new candidates (all may already be in WATCHLIST).")\n',
    "\n",
    "    # --- Section 5: Action Plan ---\n",
    "    print(f\"\\n{SEP}\")\n",
    '    print("  SECTION 5 · DAILY ACTION PLAN")\n',
    "    print(SEP)\n",
    "    if signal_matrix is not None and not signal_matrix.empty:\n",
    "        sm = signal_matrix.reset_index() if \"Ticker\" not in signal_matrix.columns else signal_matrix\n",
    '        counts = sm["Final Action Signal"].value_counts()\n',
    "        print(f\"  Signal breakdown: {dict(counts)}\")\n",
    '        top_buys = (sm[sm["Final Action Signal"] == "Buy"]\n',
    '                    .nlargest(3, "Composite")[[\"Ticker\", \"Composite\"]])\n',
    "        if not top_buys.empty:\n",
    '            print("  Top Buy candidates:")\n',
    "            for _, row in top_buys.iterrows():\n",
    "                print(f\"    → {row['Ticker']:<8}  Composite: {row['Composite']:.3f}\")\n",
    "    elif not candidates_df.empty:\n",
    '        print("  Top discovered tickers (run full pipeline for signal scores):")\n',
    "        for _, row in candidates_df.head(3).iterrows():\n",
    "            posture = \"\"\n",
    "            if tech is not None and row[\"Ticker\"] in tech:\n",
    "                try:\n",
    "                    lbl, _, _ = compute_technical_posture(tech[row[\"Ticker\"]])\n",
    "                    posture = f\"  [{lbl}]\"\n",
    "                except Exception:\n",
    "                    pass\n",
    "            print(f\"    → {row['Ticker']:<8}  Score: {row['Discovery_Score']:.3f}\"\n",
    "                  f\"  Sector: {row['Sector']}{posture}\")\n",
    "\n",
    "    # --- Enrich WATCHLIST ---\n",
    "    if not candidates_df.empty:\n",
    "        top_picks = candidates_df.head(top_n)\n",
    "        additions = {r[\"Ticker\"]: r[\"Sector\"] for _, r in top_picks.iterrows()}\n",
    "        WATCHLIST.update(additions)\n",
    "        print(f\"\\n  ✅ WATCHLIST enriched +{len(additions)} tickers → \"\n",
    "              f\"{len(WATCHLIST)} total: {list(additions.keys())}\")\n",
    "\n",
    '    print("\\n" + "=" * 70)\n',
    "\n",
    "\n",
    "print(\"Stage 0 defined: _fetch_index_data(), _index_stats(), scan_candidates(), \"\n",
    "      \"plot_index_overview(), generate_daily_overview()\")\n",
]

CALL_SOURCE = [
    "# Run Stage 0 each morning. Pass pipeline globals after Stage 4 for enriched output:\n",
    "#   generate_daily_overview(signal_matrix=signal_matrix, tech=tech)\n",
    "generate_daily_overview()\n",
]

nb = json.load(open(NB_PATH))
new_cells = [
    make_markdown_cell(MARKDOWN_SOURCE),
    make_code_cell(DEFS_SOURCE),
    make_code_cell(CALL_SOURCE),
]
nb["cells"] = nb["cells"][:7] + new_cells + nb["cells"][7:]
with open(NB_PATH, "w") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
print(f"Inserted {len(new_cells)} cells. Notebook now has {len(nb['cells'])} cells.")
```

Run it:
```bash
cd /Users/liyanglu/PycharmProjects/StockAnalysis
python3 /tmp/insert_stage0.py
```

Expected output:
```
Inserted 3 cells. Notebook now has 28 cells.
```

- [ ] **Step 2: Verify cell positions look correct**

```bash
python3 -c "
import json
nb = json.load(open('/Users/liyanglu/PycharmProjects/StockAnalysis/stock_analysis.ipynb'))
for i, c in enumerate(nb['cells']):
    src = ''.join(c['source'])
    print(f'[{i}] {c[\"cell_type\"]:8s} | {src[:90].replace(chr(10),\" | \")}')
"
```

Expected: cells [7], [8], [9] should be the new markdown + two code cells; old cell [7] (ingestion driver) should now be at index [10].

---

## Task 2: Syntax-validate the notebook

**Files:**
- Read: `stock_analysis.ipynb`

- [ ] **Step 1: Run the CLAUDE.md compile check**

```bash
cd /Users/liyanglu/PycharmProjects/StockAnalysis
python3 -c "
import json
nb = json.load(open('stock_analysis.ipynb'))
errors = []
for i, c in enumerate(nb['cells']):
    if c['cell_type'] != 'code':
        continue
    src = ''.join(c['source'])
    if src.lstrip().startswith('!'):
        continue
    try:
        compile(src, f'cell[{i}]', 'exec')
    except SyntaxError as e:
        errors.append(f'cell[{i}]: {e}')
if errors:
    print('ERRORS:')
    for e in errors: print(' ', e)
else:
    print('ok — all cells compile cleanly')
"
```

Expected output: `ok — all cells compile cleanly`

If there are syntax errors, fix them by editing the insertion script and re-running Task 1 Step 1.

---

## Task 3: End-to-end verification

**Files:**
- Read: `stock_analysis.ipynb` (run in Jupyter)

These steps require a running Jupyter kernel with network access.

- [ ] **Step 1: Standalone run (no pipeline globals)**

In Jupyter, run only cells [0]–[9] (stops after `generate_daily_overview()`). Confirm:
- All 5 sections print without error
- Plotly chart appears (2 panels: normalized index history + VIX)
- `WATCHLIST` now has more than 15 entries
- No Python exceptions raised

- [ ] **Step 2: Full pipeline run**

Run all cells top-to-bottom (Run All). Confirm:
- `signal_matrix` has rows for the original 15 + the discovered tickers
- `signal_matrix.xlsx` exports cleanly

- [ ] **Step 3: Enriched action plan**

After a full pipeline run, call in a new cell:
```python
generate_daily_overview(signal_matrix=signal_matrix, tech=tech)
```
Confirm Section 5 shows `Signal breakdown: {...}` and `Top Buy candidates:` lines.
