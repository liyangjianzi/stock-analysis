# Backtest / Signal Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline-testable backtest that replays the pipeline's signals over price history — a point-in-time forward-return event study plus a portfolio equity curve — to validate whether Bullish/Buy signals actually precede above-baseline returns.

**Architecture:** A new `backtest.py` module mirrors `pipeline.run()`: it reuses `ingest`, `indicators.add_indicators`, and `signals.compute_technical_posture`, walks each ticker bar-by-bar recomputing posture on trailing slices (so envelope percentiles and regression channels never see the future), and emits results through a new Plotly report (`charts.build_backtest_report`) and a new Excel writer (`outputs/backtest_excel.py`). A `stock-analysis backtest` CLI subcommand drives it.

**Tech Stack:** Python 3.10+, pandas, numpy, ta, plotly, openpyxl, pytest (existing offline suite).

## Global Constraints

- Python `>=3.10` (from `pyproject.toml`); use `from __future__ import annotations` in every new module, matching the codebase.
- **No network in tests.** All tests run against synthetic OHLCV fixtures (`tests/conftest.py` provides `uptrend_ohlcv`, `downtrend_ohlcv`). Network fetches (`load_watchlist`, benchmark) live only in `run_backtest`, which is *not* unit-tested.
- **Point-in-time correctness is mandatory.** Posture at date `t` must be computed only from `hist.iloc[:t+1]`. The envelope (`ENV_UP/ENV_DOWN`) and `fit_regression_channel`/`find_support_resistance` in `indicators.py` use the full window they're handed, so the timeline MUST re-run `add_indicators` on each trailing slice.
- **Composite mode carries a lookahead caveat.** yfinance gives only today's fundamentals; composite-mode output must stamp the bias warning (on the report title and CLI stdout).
- Score/units contract unchanged: fundamental score 0–6, technical score 0–`len(TECHNICAL_COMPONENTS)`, composite `0.70*(f/6) + 0.30*(t/N)` → Buy ≥0.60, Hold ≥0.40, else Watch.
- Horizons: `HORIZONS_BARS = {"1m": 21, "3m": 63, "6m": 126}` (trading days).

---

### Task 1: Module scaffold + point-in-time `posture_timeline` (technical mode)

This is the correctness-critical task: the timeline must be immune to future data.

**Files:**
- Create: `src/stockanalysis/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `add_indicators` (`indicators.py`), `compute_technical_posture`, `TECHNICAL_COMPONENTS` (`signals.py`).
- Produces:
  - `HORIZONS_BARS: dict[str, int]`
  - `posture_timeline(hist, *, mode="technical", fundamental_score=None, components=None, min_bars=60) -> pd.DataFrame` — index = dates from `min_bars` onward; columns `["tech_score", "label"]`. In technical mode `label ∈ {"Bearish","Neutral","Bullish"}`.

- [ ] **Step 1: Write the failing test** (the lookahead guard)

```python
# tests/test_backtest.py
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalysis.backtest import posture_timeline


def test_posture_timeline_is_point_in_time(uptrend_ohlcv):
    """Truncating future bars must not change any past label/score.

    A naive impl that runs add_indicators once on the full series fails here,
    because the envelope percentiles (ENV_UP/DOWN) would peek at the future.
    """
    hist = uptrend_ohlcv
    cut = 180
    full = posture_timeline(hist, min_bars=60)
    truncated = posture_timeline(hist.iloc[:cut], min_bars=60)

    assert not truncated.empty
    common = truncated.index
    pd.testing.assert_frame_equal(full.loc[common], truncated.loc[common])


def test_posture_timeline_labels_are_technical(uptrend_ohlcv):
    tl = posture_timeline(uptrend_ohlcv, min_bars=60)
    assert set(tl.columns) == {"tech_score", "label"}
    assert set(tl["label"]).issubset({"Bearish", "Neutral", "Bullish"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backtest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockanalysis.backtest'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/stockanalysis/backtest.py
"""Signal backtest: point-in-time posture replay, forward-return event study,
and a portfolio equity curve.

Correctness rule: posture at date ``t`` is computed only from ``hist.iloc[:t+1]``.
The envelope band and regression/support overlays in :mod:`indicators` read the
whole window they are handed, so :func:`posture_timeline` re-runs
``add_indicators`` on each trailing slice rather than once on the full series.
This is O(N^2) per ticker — fine for a watchlist of dozens over a few years.
"""
from __future__ import annotations

import pandas as pd

from .indicators import add_indicators
from .signals import TECHNICAL_COMPONENTS, compute_technical_posture

#: Forward-return horizons in trading days.
HORIZONS_BARS: dict[str, int] = {"1m": 21, "3m": 63, "6m": 126}


def posture_timeline(hist, *, mode="technical", fundamental_score=None,
                     components=None, min_bars: int = 60) -> pd.DataFrame:
    """Replay posture bar-by-bar, point-in-time.

    Returns a DataFrame indexed by date (from ``min_bars`` onward) with columns
    ``tech_score`` (0-len(components)) and ``label``. In ``technical`` mode
    ``label`` is the posture (Bearish/Neutral/Bullish); in ``composite`` mode it
    is the fused action (Buy/Hold/Watch) using ``fundamental_score``.
    """
    cols = ["tech_score", "label"]
    if hist is None or hist.empty or "Close" not in hist or len(hist) <= min_bars:
        return pd.DataFrame(columns=cols)

    comps = TECHNICAL_COMPONENTS if components is None else components
    n_comp = len(comps)
    f = 0.0 if fundamental_score is None else float(fundamental_score)

    out: dict = {}
    for i in range(min_bars, len(hist)):
        enriched = add_indicators(hist.iloc[: i + 1])           # trailing-only
        posture, tscore, _ = compute_technical_posture(enriched, components=comps)
        if mode == "composite":
            composite = 0.70 * (f / 6.0) + 0.30 * (tscore / n_comp)
            label = "Buy" if composite >= 0.60 else "Hold" if composite >= 0.40 else "Watch"
        else:
            label = posture
        out[hist.index[i]] = {"tech_score": tscore, "label": label}

    return pd.DataFrame.from_dict(out, orient="index", columns=cols)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backtest.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): point-in-time posture_timeline with lookahead guard"
```

---

### Task 2: Composite-mode labels in `posture_timeline`

**Files:**
- Modify: `src/stockanalysis/backtest.py` (already supports `mode="composite"` from Task 1 — this task adds the test that locks the behavior)
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `posture_timeline(..., mode="composite", fundamental_score=...)`.
- Produces: no new symbol; verifies composite labels ∈ `{"Buy","Hold","Watch"}` and depend on `fundamental_score`.

- [ ] **Step 1: Write the failing test**

```python
def test_posture_timeline_composite_uses_fundamentals(uptrend_ohlcv):
    low = posture_timeline(uptrend_ohlcv, mode="composite", fundamental_score=0)
    high = posture_timeline(uptrend_ohlcv, mode="composite", fundamental_score=6)

    assert set(low["label"]).issubset({"Buy", "Hold", "Watch"})
    # With a strong uptrend, raising the fundamental score can only push the
    # composite up, so the count of "Buy" labels must be >= the low-score count.
    assert (high["label"] == "Buy").sum() >= (low["label"] == "Buy").sum()
```

- [ ] **Step 2: Run test to verify it fails (if behavior regressed) or passes**

Run: `pytest tests/test_backtest.py::test_posture_timeline_composite_uses_fundamentals -v`
Expected: PASS (the implementation from Task 1 already supports this). If it fails, fix `posture_timeline` composite branch before continuing.

- [ ] **Step 3: (No new code unless the test failed.)**

- [ ] **Step 4: Run the full file**

Run: `pytest tests/test_backtest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_backtest.py
git commit -m "test(backtest): lock composite-mode label behavior"
```

---

### Task 3: `entry_events` — de-overlapped entry transitions

**Files:**
- Modify: `src/stockanalysis/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: a timeline DataFrame (from `posture_timeline`) with a `label` column.
- Produces: `entry_events(timeline, entry_labels=("Bullish",)) -> list[pd.Timestamp]` — only the bars where `label` *transitions into* `entry_labels` (consecutive in-label days collapse to one event).

- [ ] **Step 1: Write the failing test**

```python
from stockanalysis.backtest import entry_events


def test_entry_events_collapse_consecutive_bullish():
    idx = pd.bdate_range("2023-01-02", periods=6)
    tl = pd.DataFrame(
        {"tech_score": [0, 0, 0, 0, 0, 0],
         "label": ["Neutral", "Bullish", "Bullish", "Neutral", "Bullish", "Bullish"]},
        index=idx,
    )
    events = entry_events(tl, entry_labels=("Bullish",))
    # Two runs of Bullish -> two entry events, at the first bar of each run.
    assert events == [idx[1], idx[4]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest.py::test_entry_events_collapse_consecutive_bullish -v`
Expected: FAIL — `ImportError: cannot import name 'entry_events'`

- [ ] **Step 3: Write minimal implementation** (append to `backtest.py`)

```python
def entry_events(timeline, entry_labels=("Bullish",)) -> list:
    """Dates where ``label`` transitions *into* ``entry_labels`` (de-overlapped).

    Collapsing runs of consecutive in-label bars to their first bar prevents
    autocorrelated daily samples from inflating the event count.
    """
    if timeline is None or timeline.empty or "label" not in timeline:
        return []
    is_in = timeline["label"].isin(entry_labels)
    prev = is_in.shift(1, fill_value=False)
    return list(timeline.index[is_in & ~prev])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest.py::test_entry_events_collapse_consecutive_bullish -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): de-overlapped entry_events"
```

---

### Task 4: `forward_returns` — next-open entry, horizon-close exits

**Files:**
- Modify: `src/stockanalysis/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: a ticker's OHLCV `hist`, a list of entry dates, `HORIZONS_BARS`.
- Produces: `forward_returns(hist, entry_dates, horizons=("1m","3m","6m")) -> pd.DataFrame` — index = entry dates that had room, one column per horizon label, value = `Close[i+1+h] / Open[i+1] - 1` (entry at next-day open; `NaN` if insufficient forward bars).

- [ ] **Step 1: Write the failing test**

```python
from stockanalysis.backtest import forward_returns


def test_forward_returns_one_month_horizon():
    # Entry executes at the NEXT bar's open; 1m horizon = 21 trading days later.
    n = 40
    closes = pd.Series(
        np.linspace(100.0, 139.0, n), index=pd.bdate_range("2023-01-02", periods=n)
    )
    hist = pd.DataFrame({"Open": closes.shift(1).fillna(closes.iloc[0]), "Close": closes})
    fr = forward_returns(hist, [hist.index[0]], horizons=("1m",))
    entry = hist["Open"].iloc[1]                      # next-day open
    expected = hist["Close"].iloc[1 + 21] / entry - 1
    assert np.isclose(fr.loc[hist.index[0], "1m"], expected)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest.py::test_forward_returns_one_month_horizon -v`
Expected: FAIL — `ImportError: cannot import name 'forward_returns'`

- [ ] **Step 3: Write minimal implementation** (append to `backtest.py`)

```python
import numpy as np  # add to the imports at the top of backtest.py


def forward_returns(hist, entry_dates, horizons=("1m", "3m", "6m")) -> pd.DataFrame:
    """Forward returns from a next-day-open entry to each horizon's close."""
    horizons = list(horizons)
    if hist is None or hist.empty or not entry_dates:
        return pd.DataFrame(columns=horizons)

    opens = hist["Open"].to_numpy(float)
    closes = hist["Close"].to_numpy(float)
    pos = {ts: i for i, ts in enumerate(hist.index)}
    n = len(hist)

    rows: dict = {}
    for ts in entry_dates:
        i = pos.get(ts)
        if i is None or i + 1 >= n:
            continue
        entry = opens[i + 1]                          # execute at next-day open
        if not np.isfinite(entry) or entry <= 0:
            continue
        rec = {}
        for h in horizons:
            j = i + 1 + HORIZONS_BARS[h]
            rec[h] = (closes[j] / entry - 1) if j < n and np.isfinite(closes[j]) else np.nan
        rows[ts] = rec

    return pd.DataFrame.from_dict(rows, orient="index", columns=horizons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest.py::test_forward_returns_one_month_horizon -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): forward_returns with next-open entry"
```

---

### Task 5: `aggregate_event_stats` + `yearly_means`

**Files:**
- Modify: `src/stockanalysis/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: an event-returns DataFrame (from `forward_returns`) and an optional baseline DataFrame.
- Produces:
  - `aggregate_event_stats(event_returns, baseline_returns=None) -> dict[str, dict]` — per horizon: `n, hit_rate, mean, median, avg_win, avg_loss` (+ `baseline_mean, excess_mean` when a baseline is given).
  - `yearly_means(event_returns) -> dict[str, dict[int, float]]` — per horizon → {year: mean return}.

- [ ] **Step 1: Write the failing test**

```python
from stockanalysis.backtest import aggregate_event_stats, yearly_means


def test_aggregate_event_stats_basic():
    idx = pd.bdate_range("2023-01-02", periods=4)
    ev = pd.DataFrame({"1m": [0.10, -0.05, 0.20, np.nan]}, index=idx)
    base = pd.DataFrame({"1m": [0.01, 0.01, 0.01, 0.01]}, index=idx)
    stats = aggregate_event_stats(ev, base)["1m"]

    assert stats["n"] == 3
    assert np.isclose(stats["hit_rate"], 2 / 3)
    assert np.isclose(stats["mean"], (0.10 - 0.05 + 0.20) / 3)
    assert np.isclose(stats["baseline_mean"], 0.01)
    assert np.isclose(stats["excess_mean"], stats["mean"] - 0.01)


def test_yearly_means_groups_by_year():
    idx = [pd.Timestamp("2022-06-01"), pd.Timestamp("2023-06-01")]
    ev = pd.DataFrame({"1m": [0.10, 0.20]}, index=idx)
    ym = yearly_means(ev)
    assert np.isclose(ym["1m"][2022], 0.10)
    assert np.isclose(ym["1m"][2023], 0.20)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backtest.py::test_aggregate_event_stats_basic tests/test_backtest.py::test_yearly_means_groups_by_year -v`
Expected: FAIL — `ImportError: cannot import name 'aggregate_event_stats'`

- [ ] **Step 3: Write minimal implementation** (append to `backtest.py`)

```python
def aggregate_event_stats(event_returns, baseline_returns=None) -> dict:
    """Per-horizon hit-rate / mean / median / win-loss, optionally baseline-relative."""
    stats: dict = {}
    for h in event_returns.columns:
        s = event_returns[h].dropna()
        wins, losses = s[s > 0], s[s < 0]
        d = {
            "n": int(s.size),
            "hit_rate": float((s > 0).mean()) if s.size else float("nan"),
            "mean": float(s.mean()) if s.size else float("nan"),
            "median": float(s.median()) if s.size else float("nan"),
            "avg_win": float(wins.mean()) if wins.size else float("nan"),
            "avg_loss": float(losses.mean()) if losses.size else float("nan"),
        }
        if baseline_returns is not None and h in baseline_returns:
            b = baseline_returns[h].dropna()
            d["baseline_mean"] = float(b.mean()) if b.size else float("nan")
            d["excess_mean"] = (d["mean"] - d["baseline_mean"]
                                if s.size and b.size else float("nan"))
        stats[h] = d
    return stats


def yearly_means(event_returns) -> dict:
    """Per-horizon mean forward return grouped by entry year (regime robustness)."""
    if event_returns is None or event_returns.empty:
        return {}
    by_year = event_returns.groupby(event_returns.index.year).mean()
    return {h: by_year[h].dropna().to_dict() for h in event_returns.columns}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backtest.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): event-study aggregation + year-by-year means"
```

---

### Task 6: `simulate_portfolio` — equity curve + closed-trade stats

**Files:**
- Modify: `src/stockanalysis/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `prices` (dict ticker→OHLCV), `timeline_map` (dict ticker→timeline), `entry_events`.
- Produces: `simulate_portfolio(prices, timeline_map, *, entry_labels=("Bullish",), max_positions=10, max_hold_bars=63, cost_bps=10.0, slippage_mult=1.0, start_cash=100_000.0) -> dict` with keys `curve` (pd.Series equity by date), `summary` (dict), `trades` (list of closed-trade return pcts). Helper `_portfolio_summary(equity, trades) -> dict` with `total_return, cagr, max_drawdown, n_trades, win_rate, avg_win, avg_loss, years`.

Execution model: enter on a transition bar at that bar's **close** (cost added), exit at the close of the bar where the label leaves `entry_labels` or `max_hold_bars` elapses. Fixed slot sizing (`start_cash / max_positions`); events with no free slot or insufficient cash are skipped.

- [ ] **Step 1: Write the failing test**

```python
from stockanalysis.backtest import simulate_portfolio


def test_simulate_portfolio_runs_a_winning_trade():
    idx = pd.bdate_range("2023-01-02", periods=6)
    closes = pd.Series([100, 100, 110, 120, 130, 140], index=idx)
    hist = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": 1_000_000})
    prices = {"AAA": hist}
    # Enter on bar 1 (transition into Bullish), hold, force exit by max_hold.
    tl = pd.DataFrame(
        {"tech_score": [0] * 6,
         "label": ["Neutral", "Bullish", "Bullish", "Bullish", "Bullish", "Bullish"]},
        index=idx,
    )
    out = simulate_portfolio(prices, {"AAA": tl}, max_positions=1,
                             max_hold_bars=3, cost_bps=0.0, start_cash=1_000.0)

    assert not out["curve"].empty
    assert out["summary"]["n_trades"] == 1
    assert out["summary"]["win_rate"] == 1.0           # bought ~110, exited ~140
    assert out["curve"].iloc[-1] > 1_000.0             # equity grew
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest.py::test_simulate_portfolio_runs_a_winning_trade -v`
Expected: FAIL — `ImportError: cannot import name 'simulate_portfolio'`

- [ ] **Step 3: Write minimal implementation** (append to `backtest.py`)

```python
def _portfolio_summary(equity, trades) -> dict:
    if equity is None or equity.empty:
        return {"total_return": float("nan"), "cagr": float("nan"),
                "max_drawdown": float("nan"), "n_trades": 0,
                "win_rate": float("nan"), "avg_win": float("nan"),
                "avg_loss": float("nan"), "years": 0.0}
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    days = (equity.index[-1] - equity.index[0]).days
    years = days / 365.25 if days else 0.0
    cagr = (end / start) ** (1 / years) - 1 if years > 0 and start > 0 else float("nan")
    max_dd = float((equity / equity.cummax() - 1).min())
    t = pd.Series(trades, dtype=float)
    wins, losses = t[t > 0], t[t < 0]
    return {
        "total_return": end / start - 1,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "n_trades": int(t.size),
        "win_rate": float((t > 0).mean()) if t.size else float("nan"),
        "avg_win": float(wins.mean()) if wins.size else float("nan"),
        "avg_loss": float(losses.mean()) if losses.size else float("nan"),
        "years": years,
    }


def simulate_portfolio(prices, timeline_map, *, entry_labels=("Bullish",),
                       max_positions=10, max_hold_bars=63, cost_bps=10.0,
                       slippage_mult=1.0, start_cash=100_000.0) -> dict:
    """Equal-slot long-only simulation over the union calendar of all tickers."""
    cost = cost_bps / 10_000.0 * slippage_mult
    closes, labels, ipos, entries = {}, {}, {}, {}
    for tk, hist in prices.items():
        tl = timeline_map.get(tk)
        if hist is None or hist.empty or tl is None or tl.empty:
            continue
        closes[tk] = hist["Close"]
        labels[tk] = tl["label"].reindex(hist.index)
        ipos[tk] = {ts: i for i, ts in enumerate(hist.index)}
        entries[tk] = set(entry_events(tl, entry_labels))

    if not closes:
        empty = pd.Series(dtype=float)
        return {"curve": empty, "summary": _portfolio_summary(empty, []), "trades": []}

    calendar = sorted(set().union(*[set(c.index) for c in closes.values()]))
    cash = start_cash
    slot = start_cash / max_positions
    positions: dict = {}     # tk -> {shares, entry_pos, cost_basis}
    trades: list = []
    curve: dict = {}

    for date in calendar:
        # 1) exits
        for tk in list(positions):
            i = ipos[tk].get(date)
            if i is None:
                continue
            p = positions[tk]
            held = i - p["entry_pos"]
            lab = labels[tk].get(date)
            left = (lab not in entry_labels) if lab is not None else False
            if held >= max_hold_bars or left:
                px = float(closes[tk].iloc[i]) * (1 - cost)
                proceeds = p["shares"] * px
                cash += proceeds
                trades.append(proceeds / p["cost_basis"] - 1)
                del positions[tk]
        # 2) entries (transition into entry_labels today), filled at this close
        for tk in closes:
            if tk in positions or len(positions) >= max_positions:
                continue
            if date in entries.get(tk, ()):
                i = ipos[tk].get(date)
                if i is None:
                    continue
                px = float(closes[tk].iloc[i]) * (1 + cost)
                if not np.isfinite(px) or px <= 0 or cash < slot:
                    continue
                positions[tk] = {"shares": slot / px, "entry_pos": i, "cost_basis": slot}
                cash -= slot
        # 3) mark-to-market
        mtm = 0.0
        for tk, p in positions.items():
            i = ipos[tk].get(date)
            px = float(closes[tk].iloc[i]) if i is not None else np.nan
            mtm += p["shares"] * (px if np.isfinite(px) else 0.0)
        curve[date] = cash + mtm

    equity = pd.Series(curve).sort_index()
    return {"curve": equity, "summary": _portfolio_summary(equity, trades), "trades": trades}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest.py::test_simulate_portfolio_runs_a_winning_trade -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/backtest.py tests/test_backtest.py
git commit -m "feat(backtest): equal-slot portfolio simulation + summary"
```

---

### Task 7: `BacktestResults` dataclass + `run_backtest` orchestrator

**Files:**
- Modify: `src/stockanalysis/backtest.py`
- Modify: `src/stockanalysis/__init__.py:14-21` (export the new symbols)
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `load_watchlist`, `fetch_stock_data` (`ingest.py`), `screen_fundamentals` (`screener.py`), `charts.build_backtest_report`/`save_html` (Task 8), `write_backtest_workbook` (Task 9). To keep tasks independently testable, **Task 7 imports `charts`/`write_backtest_workbook` lazily inside `run_backtest`** so Tasks 8–9 can be implemented after it without breaking imports.
- Produces:
  - `@dataclass BacktestResults` with fields: `mode, event_stats, yearly, portfolio_curve, portfolio_summary, benchmark_curve, per_ticker_returns, config, report_path, excel_path`.
  - `run_backtest(watchlist=None, period="5y", *, mode="technical", horizons=("1m","3m","6m"), max_hold="3m", max_positions=10, cost_bps=10.0, slippage_mult=1.0, benchmark="SPY", out_dir="output/backtest", export_excel=True, save_report=True) -> BacktestResults`
  - `_bars(label) -> int` (maps "1m"/"3m"/"6m" via `HORIZONS_BARS`, else `int(label)`).

This task is verified by a fully offline test that injects synthetic `prices` and asserts the assembled event-study/portfolio wiring — the network paths (`load_watchlist`, benchmark) are exercised only via the CLI, never in tests.

- [ ] **Step 1: Write the failing test**

```python
from stockanalysis.backtest import build_results_from_prices  # thin, testable core


def test_build_results_from_prices_offline(uptrend_ohlcv, downtrend_ohlcv):
    prices = {"UP": uptrend_ohlcv, "DOWN": downtrend_ohlcv}
    res = build_results_from_prices(prices, mode="technical",
                                    horizons=("1m", "3m"), max_hold="1m",
                                    max_positions=2, cost_bps=10.0, slippage_mult=1.0)
    assert res.mode == "technical"
    assert "Bullish" in res.event_stats
    assert set(res.event_stats["Bullish"]).issubset({"1m", "3m"})
    assert isinstance(res.portfolio_summary, dict)
    assert "max_drawdown" in res.portfolio_summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest.py::test_build_results_from_prices_offline -v`
Expected: FAIL — `ImportError: cannot import name 'build_results_from_prices'`

- [ ] **Step 3: Write minimal implementation** (append to `backtest.py`; add imports)

```python
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config  # noqa: F401  (kept for parity / future use)


def _bars(label) -> int:
    return HORIZONS_BARS[label] if isinstance(label, str) and label in HORIZONS_BARS else int(label)


@dataclass
class BacktestResults:
    mode: str = "technical"
    event_stats: dict = field(default_factory=dict)          # bucket -> horizon -> stats
    yearly: dict = field(default_factory=dict)               # horizon -> {year: mean}
    portfolio_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    portfolio_summary: dict = field(default_factory=dict)
    benchmark_curve: "pd.Series | None" = None
    per_ticker_returns: dict = field(default_factory=dict)    # ticker -> forward-returns df
    config: dict = field(default_factory=dict)
    report_path: "str | None" = None
    excel_path: "str | None" = None


def build_results_from_prices(prices, *, mode="technical", fundamental_scores=None,
                              horizons=("1m", "3m", "6m"), max_hold="3m",
                              max_positions=10, cost_bps=10.0,
                              slippage_mult=1.0) -> BacktestResults:
    """Assemble a BacktestResults from an in-memory price dict (no network).

    This is the offline-testable core of :func:`run_backtest`.
    """
    fundamental_scores = fundamental_scores or {}
    horizons = list(horizons)
    entry_labels = ("Buy",) if mode == "composite" else ("Bullish",)
    bucket = entry_labels[0]

    timeline_map, ev_returns, base_returns, per_ticker = {}, [], [], {}
    for tk, hist in prices.items():
        tl = posture_timeline(hist, mode=mode, fundamental_score=fundamental_scores.get(tk))
        if tl.empty:
            continue
        timeline_map[tk] = tl
        ev = forward_returns(hist, entry_events(tl, entry_labels), horizons)
        per_ticker[tk] = ev
        if not ev.empty:
            ev_returns.append(ev)
        base_returns.append(forward_returns(hist, list(hist.index[:-1]), horizons))

    ev_all = pd.concat(ev_returns) if ev_returns else pd.DataFrame(columns=horizons)
    base_all = pd.concat(base_returns) if base_returns else pd.DataFrame(columns=horizons)

    port = simulate_portfolio(prices, timeline_map, entry_labels=entry_labels,
                              max_positions=max_positions, max_hold_bars=_bars(max_hold),
                              cost_bps=cost_bps, slippage_mult=slippage_mult)

    return BacktestResults(
        mode=mode,
        event_stats={bucket: aggregate_event_stats(ev_all, base_all)},
        yearly=yearly_means(ev_all),
        portfolio_curve=port["curve"],
        portfolio_summary=port["summary"],
        per_ticker_returns=per_ticker,
        config={"mode": mode, "horizons": horizons, "max_hold": max_hold,
                "max_positions": max_positions, "cost_bps": cost_bps,
                "slippage_mult": slippage_mult, "entry_bucket": bucket},
    )


def _benchmark_curve(ticker, period, strat_curve):
    from .ingest import fetch_stock_data
    hist, _ = fetch_stock_data(ticker, period=period)
    if hist is None or hist.empty or strat_curve is None or strat_curve.empty:
        return None
    close = hist["Close"].reindex(strat_curve.index).ffill().dropna()
    if close.empty:
        return None
    return close / float(close.iloc[0]) * float(strat_curve.iloc[0])


def run_backtest(watchlist=None, period="5y", *, mode="technical",
                 horizons=("1m", "3m", "6m"), max_hold="3m", max_positions=10,
                 cost_bps=10.0, slippage_mult=1.0, benchmark="SPY",
                 out_dir="output/backtest", export_excel=True,
                 save_report=True) -> BacktestResults:
    """Network-driven entry point: fetch history, build results, write outputs."""
    from .ingest import load_watchlist
    from .screener import screen_fundamentals

    watchlist = config.load_watchlist_csv() if watchlist is None else watchlist
    prices, fundamentals_df = load_watchlist(watchlist, period=period)

    f_scores = {}
    if mode == "composite":
        screened = screen_fundamentals(fundamentals_df)
        if not screened.empty:
            f_scores = screened["Fundamental_Score"].to_dict()

    results = build_results_from_prices(
        prices, mode=mode, fundamental_scores=f_scores, horizons=horizons,
        max_hold=max_hold, max_positions=max_positions, cost_bps=cost_bps,
        slippage_mult=slippage_mult,
    )
    results.config["period"] = period

    if benchmark and not results.portfolio_curve.empty:
        results.benchmark_curve = _benchmark_curve(benchmark, period, results.portfolio_curve)

    out = Path(out_dir) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if export_excel and results.event_stats.get(results.config["entry_bucket"]):
        from .outputs.backtest_excel import write_backtest_workbook
        out.mkdir(parents=True, exist_ok=True)
        results.excel_path = write_backtest_workbook(results, out / "backtest.xlsx")
    if save_report and not results.portfolio_curve.empty:
        from . import charts
        out.mkdir(parents=True, exist_ok=True)
        fig = charts.build_backtest_report(results)
        results.report_path = charts.save_html(fig, out / "backtest_report.html")

    return results
```

- [ ] **Step 4: Export from the package** — modify `src/stockanalysis/__init__.py`

Change the import line and `__all__` to add the backtest symbols:

```python
from . import backtest, charts, config, indicators, ingest, overview, profile, screener, signals
from .backtest import BacktestResults, run_backtest
from .pipeline import Results, run, run_output_dir
```

And add `"run_backtest"`, `"BacktestResults"`, `"backtest"` to the `__all__` list.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_backtest.py::test_build_results_from_prices_offline -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/stockanalysis/backtest.py src/stockanalysis/__init__.py tests/test_backtest.py
git commit -m "feat(backtest): BacktestResults + run_backtest orchestrator"
```

---

### Task 8: `charts.build_backtest_report`

**Files:**
- Modify: `src/stockanalysis/charts.py` (append a builder; reuse `save_html`, `config.PLOT_HEIGHT`)
- Test: `tests/test_backtest_outputs.py`

**Interfaces:**
- Consumes: a `BacktestResults`.
- Produces: `build_backtest_report(results, title="Backtest Report") -> go.Figure` — 2 rows: equity curve (+ benchmark) and a hit-rate bar by horizon. Composite mode appends a lookahead warning to the title.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_outputs.py
from __future__ import annotations

import pandas as pd

from stockanalysis import charts
from stockanalysis.backtest import build_results_from_prices


def test_build_backtest_report_returns_figure(uptrend_ohlcv, downtrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv, "DOWN": downtrend_ohlcv},
                                    horizons=("1m", "3m"), max_hold="1m")
    fig = charts.build_backtest_report(res)
    assert fig is not None
    assert len(fig.data) >= 1            # at least the equity curve


def test_build_backtest_report_flags_composite(uptrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv}, mode="composite",
                                    fundamental_scores={"UP": 6}, horizons=("1m",),
                                    max_hold="1m")
    fig = charts.build_backtest_report(res)
    assert "COMPOSITE" in fig.layout.title.text.upper()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backtest_outputs.py -v`
Expected: FAIL — `AttributeError: module 'stockanalysis.charts' has no attribute 'build_backtest_report'`

- [ ] **Step 3: Write minimal implementation** (append to `charts.py`)

```python
def build_backtest_report(results, title: str = "Backtest Report") -> go.Figure:
    """Two-panel report: equity curve (+ benchmark) and hit-rate by horizon."""
    fig = make_subplots(
        rows=2, cols=1, row_heights=[0.62, 0.38], vertical_spacing=0.12,
        subplot_titles=("Equity Curve vs Benchmark", "Forward-Return Hit Rate by Horizon"),
    )

    eq = results.portfolio_curve
    if eq is not None and not eq.empty:
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name="Strategy",
                                 line=dict(color="#1F77B4")), row=1, col=1)
    bm = results.benchmark_curve
    if bm is not None and not bm.empty:
        fig.add_trace(go.Scatter(x=bm.index, y=bm.values, name="Benchmark",
                                 line=dict(color="#999999", dash="dot")), row=1, col=1)

    bucket = results.config.get("entry_bucket")
    stats = results.event_stats.get(bucket, {})
    horizons = list(stats)
    hit = [stats[h].get("hit_rate") for h in horizons]
    if horizons:
        fig.add_trace(go.Bar(x=horizons, y=hit, name="Hit rate",
                             marker_color="#2CA02C"), row=2, col=1)

    caveat = "" if results.mode == "technical" else \
        "   ⚠ COMPOSITE MODE — fundamentals frozen at today (lookahead bias)"
    fig.update_layout(title=title + caveat, height=config.PLOT_HEIGHT, showlegend=True)
    fig.update_yaxes(title_text="Equity", row=1, col=1)
    fig.update_yaxes(title_text="Hit rate", range=[0, 1], row=2, col=1)
    return fig
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backtest_outputs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/charts.py tests/test_backtest_outputs.py
git commit -m "feat(backtest): Plotly backtest report with composite caveat"
```

---

### Task 9: `outputs/backtest_excel.write_backtest_workbook`

**Files:**
- Create: `src/stockanalysis/outputs/backtest_excel.py` (reuse `_style_base` from `outputs/excel.py`)
- Test: `tests/test_backtest_outputs.py`

**Interfaces:**
- Consumes: a `BacktestResults`, an output path; `_style_base` from `outputs/excel.py`.
- Produces: `write_backtest_workbook(results, path) -> str` — writes `Backtest Summary` (one row from `portfolio_summary`) + `Event Study` (one row per bucket×horizon) sheets, styled, and returns the path string.

- [ ] **Step 1: Write the failing test** (append to `tests/test_backtest_outputs.py`)

```python
import openpyxl

from stockanalysis.outputs.backtest_excel import write_backtest_workbook


def test_write_backtest_workbook(tmp_path, uptrend_ohlcv, downtrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv, "DOWN": downtrend_ohlcv},
                                    horizons=("1m", "3m"), max_hold="1m")
    path = tmp_path / "bt.xlsx"
    out = write_backtest_workbook(res, path)

    wb = openpyxl.load_workbook(out)
    assert "Backtest Summary" in wb.sheetnames
    assert "Event Study" in wb.sheetnames
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_outputs.py::test_write_backtest_workbook -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stockanalysis.outputs.backtest_excel'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/stockanalysis/outputs/backtest_excel.py
"""Excel writer for backtest results — a Summary sheet + an Event Study sheet.

Reuses the base styling from :mod:`stockanalysis.outputs.excel` so the two
workbooks look consistent. Not an :class:`Exporter` subclass: the Exporter
contract is signal-matrix shaped, whereas a backtest produces different tables.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .excel import _style_base

log = logging.getLogger(__name__)


def write_backtest_workbook(results, path) -> str:
    """Write a styled two-sheet backtest workbook and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    summary_df = pd.DataFrame([{"mode": results.mode, **(results.portfolio_summary or {})}])

    rows = []
    for bucket, hstats in results.event_stats.items():
        for horizon, d in hstats.items():
            rows.append({"Bucket": bucket, "Horizon": horizon, **d})
    event_df = pd.DataFrame(rows)

    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        summary_df.to_excel(xl, sheet_name="Backtest Summary", index=False)
        if not event_df.empty:
            event_df.to_excel(xl, sheet_name="Event Study", index=False)
        for ws in xl.sheets.values():
            _style_base(ws)

    log.info("Wrote backtest workbook to '%s'.", path)
    return str(path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_outputs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/outputs/backtest_excel.py tests/test_backtest_outputs.py
git commit -m "feat(backtest): styled Excel workbook writer"
```

---

### Task 10: `stock-analysis backtest` CLI subcommand

**Files:**
- Modify: `src/stockanalysis/cli.py` (add `_add_backtest_parser` + a `backtest` branch in `main`)
- Test: `tests/test_cli_backtest.py`

**Interfaces:**
- Consumes: `backtest.run_backtest` (patched in the test so no network runs).
- Produces: CLI `stock-analysis backtest [--period 5y] [--scope technical|composite] [--horizon 1m|3m|6m] [--max-hold 1m|3m|6m] [--max-positions N] [--cost-bps F] [--slippage-mult F] [--out DIR] [--no-excel] [--no-report]`. Composite scope prints a loud lookahead caveat to stdout.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_backtest.py
from __future__ import annotations

from unittest import mock

import pandas as pd

from stockanalysis import cli
from stockanalysis.backtest import BacktestResults


def test_cli_backtest_invokes_run_backtest():
    fake = BacktestResults(mode="technical",
                           portfolio_summary={"total_return": 0.2, "cagr": 0.1,
                                              "max_drawdown": -0.1, "n_trades": 5,
                                              "win_rate": 0.6, "years": 2.0},
                           config={"entry_bucket": "Bullish"})
    with mock.patch("stockanalysis.backtest.run_backtest", return_value=fake) as m:
        rc = cli.main(["backtest", "--scope", "technical", "--no-report", "--no-excel"])
    assert rc == 0
    assert m.called
    assert m.call_args.kwargs["mode"] == "technical"


def test_cli_backtest_composite_warns(capsys):
    fake = BacktestResults(mode="composite", config={"entry_bucket": "Buy"})
    with mock.patch("stockanalysis.backtest.run_backtest", return_value=fake):
        cli.main(["backtest", "--scope", "composite", "--no-report", "--no-excel"])
    out = capsys.readouterr().out.upper()
    assert "LOOKAHEAD" in out or "CAVEAT" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_backtest.py -v`
Expected: FAIL — argparse rejects the unknown `backtest` subcommand (SystemExit).

- [ ] **Step 3: Write minimal implementation** — edit `cli.py`

Add the parser builder (after `_add_run_parser`):

```python
def _add_backtest_parser(sub) -> None:
    p = sub.add_parser("backtest", help="Backtest the signal engine over history.")
    p.add_argument("--scope", choices=["technical", "composite"], default="technical",
                   help="technical = price-only (no lookahead); composite = full "
                        "Buy/Hold/Watch with TODAY's fundamentals (lookahead-biased).")
    p.add_argument("--period", default="5y", help="yfinance history period (default: 5y).")
    p.add_argument("--horizon", choices=["1m", "3m", "6m"], action="append", default=None,
                   help="Forward-return horizon(s); repeatable (default: 1m 3m 6m).")
    p.add_argument("--max-hold", choices=["1m", "3m", "6m"], default="3m",
                   help="Max holding period for the portfolio sim (default: 3m).")
    p.add_argument("--max-positions", type=int, default=10,
                   help="Max concurrent positions (default: 10).")
    p.add_argument("--cost-bps", type=float, default=10.0,
                   help="Round-trip cost per side in basis points (default: 10).")
    p.add_argument("--slippage-mult", type=float, default=1.0,
                   help="Multiply costs to stress-test execution (e.g. 1.5, 2.0).")
    p.add_argument("--out", default="output/backtest", help="Output base directory.")
    p.add_argument("--no-excel", action="store_true", help="Skip the Excel workbook.")
    p.add_argument("--no-report", action="store_true", help="Skip the HTML report.")
```

Register it in `build_parser` next to `_add_run_parser(sub)`:

```python
    _add_run_parser(sub)
    _add_backtest_parser(sub)
```

Add the handler branch in `main` (after the `run` branch, before `return 1`):

```python
    if args.command == "backtest":
        from . import backtest as bt

        horizons = tuple(args.horizon) if args.horizon else ("1m", "3m", "6m")
        if args.scope == "composite":
            print("⚠ COMPOSITE SCOPE: fundamentals are frozen at TODAY's values, so "
                  "past composites are LOOKAHEAD-BIASED. Treat results as a sanity "
                  "check, not proof of edge.")
        try:
            results = bt.run_backtest(
                period=args.period, mode=args.scope, horizons=horizons,
                max_hold=args.max_hold, max_positions=args.max_positions,
                cost_bps=args.cost_bps, slippage_mult=args.slippage_mult,
                out_dir=args.out, export_excel=not args.no_excel,
                save_report=not args.no_report,
            )
        except Exception as e:
            print(f"Backtest failed: {e}", file=sys.stderr)
            return 1

        s = results.portfolio_summary or {}
        print(f"\nBacktest ({results.mode}) done.")
        if s:
            print(f"  Trades: {s.get('n_trades')}  Win rate: {s.get('win_rate')}  "
                  f"Total return: {s.get('total_return')}  Max DD: {s.get('max_drawdown')}")
        if results.excel_path:
            print(f"  Workbook: {results.excel_path}")
        if results.report_path:
            print(f"  Report:   {results.report_path}")
        return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_backtest.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/stockanalysis/cli.py tests/test_cli_backtest.py
git commit -m "feat(backtest): stock-analysis backtest CLI subcommand"
```

---

### Task 11: Full-suite green + README documentation

**Files:**
- Modify: `README.md` (document the backtest command + scope caveat)
- (No code; this task verifies the whole suite passes and documents the feature.)

- [ ] **Step 1: Run the full offline suite**

Run: `pytest`
Expected: PASS — all pre-existing tests plus the new `tests/test_backtest.py`, `tests/test_backtest_outputs.py`, `tests/test_cli_backtest.py`.

- [ ] **Step 2: Add a README section** (after the "Run (CLI)" section)

```markdown
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
```

- [ ] **Step 3: Verify the docs render / import works**

Run: `PYTHONPATH=src python3 -c "import stockanalysis; print(stockanalysis.run_backtest)"`
Expected: prints the function object, no ImportError.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(backtest): document the backtest command and scope caveat"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| Technical scope (point-in-time, no lookahead) | 1 (timeline + lookahead test) |
| Composite scope (frozen fundamentals + caveat) | 2, 8 (report caveat), 10 (CLI warning), 11 (README) |
| New `backtest.py` + `run_backtest()` orchestrator | 1–7 |
| Bar-by-bar point-in-time recomputation | 1 |
| Entry events = de-overlapped transitions | 3 |
| Forward-return event study (1/3/6m, hit-rate, expectancy, baseline, year-by-year) | 4, 5 |
| Portfolio sim (equal-weight, max-hold, costs, slippage-mult, summary) | 6 |
| `BacktestResults` dataclass | 7 |
| Excel sheets via existing styling | 9 |
| Plotly report (equity + distributions) | 8 |
| CLI subcommand w/ flags + caveat | 10 |
| Lookahead guard test + offline tests | 1, 5, 6, 8, 9, 10 |
| YAGNI: no walk-forward optimization / intraday / shorting | honored (not built) |

Gap check: the spec mentions reporting **both** the de-overlapped event count *and* the naive daily count "for comparison." Task 5's `aggregate_event_stats` reports the de-overlapped `n`; the naive daily figure is available from the baseline frame (`base_all` row count) but is not surfaced as a labeled stat. **Acceptable for v1** — the honest `n` is what gates statistical confidence; add the naive count to the Summary sheet later if wanted. Noted here so it is a conscious omission, not an oversight.

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Every test has concrete asserts; every code step shows complete code.

**3. Type consistency:** `posture_timeline` returns `["tech_score","label"]` (Tasks 1–2), consumed by `entry_events` via `label` (Task 3) and `simulate_portfolio` via `timeline_map[tk]["label"]` (Task 6). `forward_returns` returns a horizon-columned DataFrame (Task 4) consumed by `aggregate_event_stats`/`yearly_means` (Task 5) and `build_results_from_prices` (Task 7). `BacktestResults.event_stats` is `{bucket: {horizon: stats}}` — written in Task 7, read by `charts.build_backtest_report` (Task 8) and `write_backtest_workbook` (Task 9) identically. `config["entry_bucket"]` set in Task 7, read in Tasks 8/10. Consistent.
