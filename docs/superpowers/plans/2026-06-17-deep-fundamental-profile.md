# Deep Fundamental Profile (Step 2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `fetch_profile()` + `analyze_ticker()` to `stock_analysis.ipynb` as a new Step 2b section — a 5-section per-ticker deep fundamental profile covering business overview, financial ratios, management quality, moat heuristics, and long-term potential.

**Architecture:** Three new cells (markdown header + function definitions + demo call) inserted at position 15 in the notebook JSON (immediately after the existing Step 2 screener display cell [14]). Zero changes to existing cells, `screened_df`, or the signal pipeline. `fetch_profile()` hits yfinance on demand; `analyze_ticker()` formats and prints to stdout.

**Tech Stack:** `yfinance`, `pandas`, `numpy` — all already imported in cell [3]. No new dependencies.

## Global Constraints

- NaN = fail, never crash: all yfinance calls wrapped in `try/except`; missing numeric fields → `np.nan` via existing `_safe(info, key)` helper (defined in cell [6]); missing strings → `""`
- No new `pip install` or imports — every library is already in scope from cell [3]
- Do not modify any existing cell (screen_fundamentals, screened_df, signal pipeline are untouched)
- Score ranges: Moat 0–3, Management 0–3, Long-Term 0–4 — display only, do not feed Fundamental_Score (0–6)
- Notebook path: `/Users/liyanglu/PycharmProjects/StockAnalysis/stock_analysis.ipynb`

---

## File Map

| File | Action | What changes |
|---|---|---|
| `stock_analysis.ipynb` | Modify | Insert 3 cells at position 15 (after screener display cell [14]) |

---

## Task 1: Insert Step 2b cells into the notebook

**Files:**
- Modify: `stock_analysis.ipynb` (insert at cell index 15)

**Interfaces:**
- Consumes: `_safe(info, key)` from cell [6] (already defined when Step 2b runs); `screened_df` (optional, produced by cell [14]); `yf`, `pd`, `np` from cell [3]
- Produces: `fetch_profile(ticker)` → `dict`; `_fmt_val(val, fmt)` → `str`; `analyze_ticker(ticker, screened_df=None)` → `None`

- [ ] **Step 1: Write the insertion script to `/tmp/insert_step2b.py`**

Write exactly this file:

```python
import json

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
    "## Step 2b · Deep Fundamental Profile\n",
    "\n",
    "`analyze_ticker(ticker, screened_df)` prints a rich 5-section report for any\n",
    "watchlist stock: business overview, extended financial ratios, management quality\n",
    "proxies, moat heuristics, and long-term potential. The existing 6-point score\n",
    "and signal pipeline are **unchanged** — this is purely additive.\n",
]

DEFS_SOURCE = [
    "# =============================================================================\n",
    "# Step 2b · Deep Fundamental Profile\n",
    "# =============================================================================\n",
    "\n",
    "def fetch_profile(ticker: str) -> dict:\n",
    '    """Fetch extended yfinance fields for a deep fundamental profile.\n',
    "\n",
    "    Returns a flat dict. Numeric values are floats or np.nan; strings are str\n",
    "    or '' on missing. Reuses existing _safe() helper. Never raises.\n",
    '    """\n',
    "    _EMPTY = {\n",
    '        "shortName": "", "longBusinessSummary": "", "sector": "",\n',
    '        "industry": "", "country": "", "numberOfEmployees": np.nan,\n',
    '        "grossMargins": np.nan, "operatingMargins": np.nan,\n',
    '        "profitMargins": np.nan, "returnOnAssets": np.nan,\n',
    '        "returnOnEquity": np.nan, "earningsGrowth": np.nan,\n',
    '        "revenueGrowth": np.nan, "currentRatio": np.nan,\n',
    '        "quickRatio": np.nan, "totalCash": np.nan,\n',
    '        "priceToBook": np.nan, "priceToSalesTrailing12Months": np.nan,\n',
    '        "pegRatio": np.nan, "enterpriseToEbitda": np.nan,\n',
    '        "heldPercentInsiders": np.nan, "heldPercentInstitutions": np.nan,\n',
    '        "shortPercentOfFloat": np.nan,\n',
    "    }\n",
    "    try:\n",
    "        info = yf.Ticker(ticker).info or {}\n",
    "        result = dict(_EMPTY)\n",
    "        # String fields\n",
    '        for key in ("longBusinessSummary", "sector", "industry", "country"):\n',
    "            val = info.get(key, \"\")\n",
    '            result[key] = str(val) if val else ""\n',
    '        result["shortName"] = (\n',
    '            info.get("shortName") or info.get("longName") or ticker\n',
    "        ).upper()\n",
    "        # Numeric fields — all fractional in yfinance, no normalization needed\n",
    '        for key in (\n',
    '            "numberOfEmployees", "grossMargins", "operatingMargins",\n',
    '            "profitMargins", "returnOnAssets", "returnOnEquity",\n',
    '            "earningsGrowth", "revenueGrowth", "currentRatio", "quickRatio",\n',
    '            "totalCash", "priceToBook", "priceToSalesTrailing12Months",\n',
    '            "pegRatio", "enterpriseToEbitda", "heldPercentInsiders",\n',
    '            "heldPercentInstitutions", "shortPercentOfFloat",\n',
    "        ):\n",
    "            result[key] = _safe(info, key)\n",
    "        return result\n",
    "    except Exception:\n",
    "        return dict(_EMPTY)\n",
    "\n",
    "\n",
    "def _fmt_val(val, fmt=\"pct\"):\n",
    '    """Format a single value for display; return \'—\' on NaN/None."""\n',
    "    try:\n",
    "        if val is None or (isinstance(val, float) and np.isnan(val)):\n",
    "            return \"\\u2014\"  # em dash\n",
    '        if fmt == "pct":\n',
    "            return f\"{val:.1%}\"\n",
    '        if fmt == "ratio":\n',
    "            return f\"{val:.2f}\"\n",
    '        if fmt == "cash":\n',
    "            if abs(val) >= 1e12:\n",
    "                return f\"${val/1e12:.1f}T\"\n",
    "            if abs(val) >= 1e9:\n",
    "                return f\"${val/1e9:.1f}B\"\n",
    "            if abs(val) >= 1e6:\n",
    "                return f\"${val/1e6:.0f}M\"\n",
    "            return f\"${val:,.0f}\"\n",
    '        if fmt == "int":\n',
    "            return f\"{int(val):,}\"\n",
    "        return str(val)\n",
    "    except Exception:\n",
    "        return \"\\u2014\"\n",
    "\n",
    "\n",
    "def analyze_ticker(ticker: str, screened_df=None) -> None:\n",
    '    """Print a 5-section deep fundamental profile for ticker.\n',
    "\n",
    "    Optionally pass screened_df (produced by Step 2) to reuse EPS_Growth,\n",
    "    Rev_Growth, FCF, Debt_Equity, PE, and Fundamental_Score instead of\n",
    "    re-fetching. All sections degrade gracefully when data is missing.\n",
    '    """\n',
    "    p = fetch_profile(ticker)\n",
    "    W    = 70\n",
    '    HDBL = "=" * W\n',
    '    SEP  = "-" * W\n',
    "\n",
    "    # Pull preferred values from screened_df when available\n",
    "    fund_score = None\n",
    "    pe_val = np.nan\n",
    "    eps_growth = p[\"earningsGrowth\"]\n",
    "    rev_growth = p[\"revenueGrowth\"]\n",
    "    fcf_val    = np.nan\n",
    "    debt_eq    = np.nan\n",
    "\n",
    "    if screened_df is not None and not screened_df.empty and ticker in screened_df.index:\n",
    "        row = screened_df.loc[ticker]\n",
    "        fs  = row.get(\"Fundamental_Score\", np.nan)\n",
    "        fund_score  = int(fs) if pd.notna(fs) else None\n",
    "        pe_val      = row.get(\"PE\",          np.nan)\n",
    "        eps_growth  = row.get(\"EPS_Growth\",  eps_growth)\n",
    "        rev_growth  = row.get(\"Rev_Growth\",  rev_growth)\n",
    "        fcf_val     = row.get(\"FCF\",         np.nan)\n",
    "        debt_eq     = row.get(\"Debt_Equity\", np.nan)\n",
    "\n",
    "    name     = p[\"shortName\"] or ticker.upper()\n",
    "    sector   = p[\"sector\"]   or \"\\u2014\"\n",
    "    industry = p[\"industry\"] or \"\\u2014\"\n",
    "    country  = p[\"country\"]  or \"\\u2014\"\n",
    "    emp_str  = _fmt_val(p[\"numberOfEmployees\"], \"int\")\n",
    "    fs_str   = f\"  Fundamental Score: {fund_score}/6\" if fund_score is not None else \"\"\n",
    "\n",
    "    # ── Header ──────────────────────────────────────────────────────────────\n",
    "    print(HDBL)\n",
    "    print(f\"  {name} ({ticker.upper()}) · {sector} · {industry}\")\n",
    "    print(f\"  Employees: {emp_str}  ·  Country: {country}{fs_str}\")\n",
    "    print(HDBL)\n",
    "\n",
    "    # ── Section 1: Business Overview ────────────────────────────────────────\n",
    "    print(\"\\nSECTION 1 · BUSINESS OVERVIEW\")\n",
    "    summary = (p.get(\"longBusinessSummary\") or \"\")[:400]\n",
    "    if summary:\n",
    "        words, line, lines = summary.split(), [], []\n",
    "        for w in words:\n",
    "            if len(\" \".join(line + [w])) > 66:\n",
    "                lines.append(\"  \" + \" \".join(line))\n",
    "                line = [w]\n",
    "            else:\n",
    "                line.append(w)\n",
    "        if line:\n",
    "            lines.append(\"  \" + \" \".join(line))\n",
    "        if len(p.get(\"longBusinessSummary\") or \"\") > 400:\n",
    "            lines[-1] += \"...\"\n",
    "        print(\"\\n\".join(lines))\n",
    "    else:\n",
    "        print(\"  No business summary available.\")\n",
    "\n",
    "    # ── Section 2: Financial Ratios ──────────────────────────────────────────\n",
    "    print(f\"\\n{SEP}\")\n",
    "    print(\"SECTION 2 · FINANCIAL RATIOS\")\n",
    "\n",
    "    gm   = _fmt_val(p[\"grossMargins\"],                    \"pct\")\n",
    "    opm  = _fmt_val(p[\"operatingMargins\"],                \"pct\")\n",
    "    npm  = _fmt_val(p[\"profitMargins\"],                   \"pct\")\n",
    "    roe  = _fmt_val(p[\"returnOnEquity\"],                  \"pct\")\n",
    "    roa  = _fmt_val(p[\"returnOnAssets\"],                  \"pct\")\n",
    "    rg   = _fmt_val(rev_growth,                           \"pct\")\n",
    "    eg   = _fmt_val(eps_growth,                           \"pct\")\n",
    "    cr   = _fmt_val(p[\"currentRatio\"],                    \"ratio\")\n",
    "    qr   = _fmt_val(p[\"quickRatio\"],                      \"ratio\")\n",
    "    de   = _fmt_val(debt_eq,                              \"ratio\")\n",
    "    cash = _fmt_val(p[\"totalCash\"],                       \"cash\")\n",
    "    pe_s = _fmt_val(pe_val,                               \"ratio\")\n",
    "    pb   = _fmt_val(p[\"priceToBook\"],                     \"ratio\")\n",
    "    ps   = _fmt_val(p[\"priceToSalesTrailing12Months\"],    \"ratio\")\n",
    "    peg  = _fmt_val(p[\"pegRatio\"],                        \"ratio\")\n",
    "    eveb = _fmt_val(p[\"enterpriseToEbitda\"],              \"ratio\")\n",
    "\n",
    "    C1, C2 = 22, 24\n",
    "    def _row(a, b, c=\"\"):\n",
    "        return f\"  {a:<{C1}}\\u2502  {b:<{C2}}\\u2502  {c}\"\n",
    "\n",
    "    print(_row(\"Profitability\",           \"Growth\",               \"Health\"))\n",
    "    print(_row(f\"Gross Margin  {gm}\",     f\"Revenue Growth  {rg}\", f\"Current Ratio  {cr}\"))\n",
    "    print(_row(f\"Op. Margin    {opm}\",    f\"EPS Growth     {eg}\",  f\"Quick Ratio    {qr}\"))\n",
    "    print(_row(f\"Net Margin    {npm}\",    \"\",                      f\"Debt/Equity    {de}\"))\n",
    "    print(_row(f\"ROE           {roe}\",    \"Valuation\",             f\"Total Cash     {cash}\"))\n",
    "    print(_row(f\"ROA           {roa}\",    f\"P/E {pe_s}  PEG  {peg}\", \"\"))\n",
    "    print(_row(\"\",                         f\"P/B {pb}  P/S  {ps}\",  \"\"))\n",
    "    print(_row(\"\",                         f\"EV/EBITDA    {eveb}\",  \"\"))\n",
    "\n",
    "    # ── Section 3: Management Quality ───────────────────────────────────────\n",
    "    print(f\"\\n{SEP}\")\n",
    "    print(\"SECTION 3 · MANAGEMENT QUALITY (proxies)\")\n",
    "\n",
    "    ins     = p[\"heldPercentInsiders\"]\n",
    "    inst    = p[\"heldPercentInstitutions\"]\n",
    "    short_f = p[\"shortPercentOfFloat\"]\n",
    "    is_mega = pd.notna(p[\"numberOfEmployees\"]) and p[\"numberOfEmployees\"] > 50_000\n",
    "\n",
    "    ins_s    = _fmt_val(ins,     \"pct\")\n",
    "    inst_s   = _fmt_val(inst,    \"pct\")\n",
    "    short_s  = _fmt_val(short_f, \"pct\")\n",
    "\n",
    "    ins_pass  = pd.notna(ins)     and ins     > 0.05\n",
    "    inst_pass = pd.notna(inst)    and inst    > 0.50\n",
    "    short_pass= pd.notna(short_f) and short_f < 0.05\n",
    "    mgmt_score = sum([ins_pass, inst_pass, short_pass])\n",
    "\n",
    "    ins_lbl   = (\"Strong alignment\" if ins_pass\n",
    "                 else \"Low (typical for mega-cap)\" if is_mega\n",
    "                 else \"Low insider ownership\")\n",
    "    inst_lbl  = \"High confidence\" if inst_pass else \"Below 50% threshold\"\n",
    "    short_lbl = \"Low bearish pressure\" if short_pass else \"Elevated short interest\"\n",
    "    mgmt_lbl  = {3:\"Strong\", 2:\"Neutral\", 1:\"Weak\", 0:\"Concern\"}[mgmt_score]\n",
    "\n",
    "    print(f\"  Insider Ownership:        {ins_s:>7}  \\u2192  {ins_lbl}\")\n",
    "    print(f\"  Institutional Ownership:  {inst_s:>7}  \\u2192  {inst_lbl}\")\n",
    "    print(f\"  Short Interest (Float):   {short_s:>7}  \\u2192  {short_lbl}\")\n",
    "    print(f\"  Management Score: {mgmt_score}/3  [{mgmt_lbl}]\")\n",
    "\n",
    "    # ── Section 4: Moat ──────────────────────────────────────────────────────\n",
    "    print(f\"\\n{SEP}\")\n",
    "    print(\"SECTION 4 · COMPETITIVE ADVANTAGES (moat heuristics)\")\n",
    "\n",
    "    roe_raw = p[\"returnOnEquity\"]\n",
    "    gm_raw  = p[\"grossMargins\"]\n",
    "    nm_raw  = p[\"profitMargins\"]\n",
    "\n",
    "    roe_pass2 = pd.notna(roe_raw) and roe_raw > 0.15\n",
    "    gm_pass2  = pd.notna(gm_raw)  and gm_raw  > 0.30\n",
    "    nm_pass2  = pd.notna(nm_raw)  and nm_raw  > 0.10\n",
    "    moat_score = sum([roe_pass2, gm_pass2, nm_pass2])\n",
    "\n",
    "    def _moat_row(label, val_str, passed, pass_lbl, fail_lbl):\n",
    "        sym = \"\\u2713\" if passed else \"\\u2717\"  # ✓ or ✗\n",
    "        lbl = pass_lbl if passed else fail_lbl\n",
    "        return f\"  {label:<14}{val_str:<8} {sym}  {lbl}\"\n",
    "\n",
    "    moat_lbl = {3:\"Wide Moat\", 2:\"Narrow Moat\", 1:\"Uncertain\", 0:\"Weak/None\"}[moat_score]\n",
    "    print(_moat_row(\"ROE\",          roe,  roe_pass2, \"Exceptional (>15%)\",         \"Below threshold (\\u226415%)\"))\n",
    "    print(_moat_row(\"Gross Margin\", gm,   gm_pass2,  \"Strong pricing power (>30%)\", \"Below threshold (\\u226430%)\"))\n",
    "    print(_moat_row(\"Net Margin\",   npm,  nm_pass2,  \"High profitability (>10%)\",  \"Below threshold (\\u226410%)\"))\n",
    "    print(f\"  Moat Score: {moat_score}/3  [{moat_lbl}]\")\n",
    "\n",
    "    # ── Section 5: Long-Term Potential ───────────────────────────────────────\n",
    "    print(f\"\\n{SEP}\")\n",
    "    print(\"SECTION 5 · LONG-TERM POTENTIAL\")\n",
    "\n",
    "    peg_raw = p[\"pegRatio\"]\n",
    "    peg_pass = pd.notna(peg_raw)  and peg_raw   < 1.5\n",
    "    rg_pass  = pd.notna(rev_growth) and rev_growth > 0.10\n",
    "    eg_pass  = pd.notna(eps_growth) and eps_growth > 0.15\n",
    "    fcf_pass = pd.notna(fcf_val)  and fcf_val   > 0\n",
    "    lt_score = sum([peg_pass, rg_pass, eg_pass, fcf_pass])\n",
    "\n",
    "    peg_s2   = _fmt_val(peg_raw,    \"ratio\")\n",
    "    rg_pct   = _fmt_val(rev_growth, \"pct\")\n",
    "    eg_pct   = _fmt_val(eps_growth, \"pct\")\n",
    "    fcf_s    = _fmt_val(fcf_val,    \"cash\")\n",
    "\n",
    "    peg_lbl  = (\"\\u2713 Growth undervalued (< 1.5)\" if peg_pass\n",
    "                else \"Growth priced in (< 1.5 = undervalued)\" if pd.notna(peg_raw)\n",
    "                else \"\\u2014\")\n",
    "    rg_lbl   = \"\\u2713 Strong top-line momentum (>10%)\" if rg_pass else (\"Moderate (<10%)\" if pd.notna(rev_growth) else \"\\u2014\")\n",
    "    eg_lbl   = \"\\u2713 Strong earnings power (>15%)\"    if eg_pass else (\"Moderate (<15%)\" if pd.notna(eps_growth) else \"\\u2014\")\n",
    "    fcf_lbl  = \"\\u2713 Positive cash generation\"        if fcf_pass else (\"Negative FCF\"   if pd.notna(fcf_val)   else \"\\u2014\")\n",
    "    lt_lbl   = {4:\"Exceptional\", 3:\"Strong\", 2:\"Moderate\", 1:\"Speculative\", 0:\"Caution\"}[lt_score]\n",
    "\n",
    "    print(f\"  PEG Ratio:       {peg_s2:>7}  \\u2192  {peg_lbl}\")\n",
    "    print(f\"  Revenue Growth:  {rg_pct:>7}  \\u2192  {rg_lbl}\")\n",
    "    print(f\"  EPS Growth:      {eg_pct:>7}  \\u2192  {eg_lbl}\")\n",
    "    print(f\"  FCF:             {fcf_s:>7}  \\u2192  {fcf_lbl}\")\n",
    "    print(f\"  Long-Term Score: {lt_score}/4  [{lt_lbl}]\")\n",
    "    print(HDBL)\n",
    "\n",
    "\n",
    'print("Step 2b defined: fetch_profile(), _fmt_val(), analyze_ticker()")\n',
]

CALL_SOURCE = [
    "# Deep-dive on any ticker after the screener runs.\n",
    "# Change the ticker or omit screened_df for a standalone call.\n",
    'analyze_ticker("AAPL", screened_df)\n',
]

nb = json.load(open(NB_PATH))
new_cells = [
    make_markdown_cell(MARKDOWN_SOURCE),
    make_code_cell(DEFS_SOURCE),
    make_code_cell(CALL_SOURCE),
]
# Insert after cell [14] (screener display) → at index 15
nb["cells"] = nb["cells"][:15] + new_cells + nb["cells"][15:]
with open(NB_PATH, "w") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)
print(f"Inserted {len(new_cells)} cells. Notebook now has {len(nb['cells'])} cells.")
```

- [ ] **Step 2: Run the insertion script**

```bash
python3 /tmp/insert_step2b.py
```

Expected output:
```
Inserted 3 cells. Notebook now has 32 cells.
```

- [ ] **Step 3: Verify cell positions**

```bash
python3 -c "
import json
nb = json.load(open('/Users/liyanglu/PycharmProjects/StockAnalysis/stock_analysis.ipynb'))
print(f'Total: {len(nb[\"cells\"])} cells')
for i, c in enumerate(nb['cells']):
    src = ''.join(c['source'])
    print(f'[{i}] {c[\"cell_type\"]:8s} | {src[:80].replace(chr(10),\" | \")}')
"
```

Expected: 32 total cells. Cells [15][16][17] are the new Step 2b markdown, definitions, and demo call. The old cell [15] (Step 3 markdown) is now at [18].

- [ ] **Step 4: Syntax-validate all cells**

```bash
python3 -c "
import json
nb = json.load(open('/Users/liyanglu/PycharmProjects/StockAnalysis/stock_analysis.ipynb'))
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
print('ok' if not errors else 'ERRORS: ' + str(errors))
"
```

Expected: `ok`  
(Cell [2] may show a pre-existing error due to `!pip install` after a comment line — that is pre-existing and unrelated to this change.)

---

## Task 2: End-to-end verification (manual — requires Jupyter + network)

**Files:**
- Read: `stock_analysis.ipynb` (run in Jupyter)

- [ ] **Step 1: Standalone call (no pipeline globals)**

In Jupyter, run cells [0]–[17] only (stop after the demo cell). Confirm:
- All 5 sections print without error
- Missing fields display as `—` (not crash)
- `WATCHLIST` and `screened_df` are not modified

- [ ] **Step 2: Call with screened_df**

After running through Step 2 (cell [14] must have run):
```python
analyze_ticker("AAPL", screened_df)
```
Confirm:
- Header shows `Fundamental Score: N/6`
- Section 2 Growth uses `screened_df`'s `EPS_Growth`/`Rev_Growth` values
- Section 2 Health shows `Debt_Equity` from `screened_df`

- [ ] **Step 3: TSX ticker with sparse data**

```python
analyze_ticker("BNS.TO", screened_df)
```
Confirm: all sections render; any missing field shows `—`; no exception raised.

- [ ] **Step 4: Full pipeline run**

Run all cells top-to-bottom (`Run All`). Confirm no cell errors; `signal_matrix.xlsx` exports cleanly; existing signal matrix output is unchanged.
