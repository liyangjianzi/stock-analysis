"""Signal backtest: point-in-time posture replay, forward-return event study,
and a portfolio equity curve.

Correctness rule: posture at date ``t`` is computed only from ``hist.iloc[:t+1]``.
The envelope band and regression/support overlays in :mod:`indicators` read the
whole window they are handed, so :func:`posture_timeline` re-runs
``add_indicators`` on each trailing slice rather than once on the full series.
This is O(N^2) per ticker — fine for a watchlist of dozens over a few years.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
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
    # NOTE: strict inequality (<=) means exactly min_bars rows returns empty;
    # min_bars+1 rows produces one entry (the first bar after the warmup window).
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
    """Equal-slot long-only simulation over the union calendar of all tickers.

    max_hold_bars is counted from the entry bar: a position entered at bar i
    is force-exited at bar i+max_hold_bars's close (label-based exits may trigger sooner).
    """
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
    # Forward-filled marks for valuation only: a held name with no bar on a union
    # date (e.g. a US name on a US-only holiday a TSX peer trades through) is carried
    # at its last close, not zeroed — otherwise equity craters to cash on the gap day
    # and snaps back next day, producing phantom drawdowns. Entries/exits still use
    # real bars via ``ipos`` below.
    marks = {tk: c.reindex(calendar).ffill() for tk, c in closes.items()}
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
        # 3) mark-to-market (carry last known close on no-bar dates, never $0)
        mtm = 0.0
        for tk, p in positions.items():
            px = marks[tk].get(date, np.nan)
            mtm += p["shares"] * (px if np.isfinite(px) else 0.0)
        curve[date] = cash + mtm

    equity = pd.Series(curve).sort_index()
    return {"curve": equity, "summary": _portfolio_summary(equity, trades), "trades": trades}


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
    per_ticker_returns: dict = field(default_factory=dict)   # ticker -> forward-returns df
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
