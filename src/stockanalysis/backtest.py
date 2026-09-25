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

from . import config, robustness
from .indicators import add_indicators
from .tradeplan import build_trade_plan
from .signals import (TECHNICAL_COMPONENTS, _components,
                      compute_technical_posture, decide_action)

#: Forward-return horizons in trading days.
HORIZONS_BARS: dict[str, int] = {"1m": 21, "3m": 63, "6m": 126}

#: Bars of history before the replay scores its first bar — shared with the
#: random-entry null so it draws from exactly the bars the gate could fire on.
WARMUP_BARS = 60


def posture_timeline(hist, *, mode="technical", fundamental_score=None,
                     components=None, min_bars: int = WARMUP_BARS,
                     fast: bool = False) -> pd.DataFrame:
    """Replay posture bar-by-bar, point-in-time.

    Returns a DataFrame indexed by date (from ``min_bars`` onward) with columns
    ``tech_score`` (0-len(components)) and ``label``. In ``technical`` mode
    ``label`` is the posture (Bearish/Neutral/Bullish); in ``composite`` mode it
    is :func:`~stockanalysis.signals.decide_action`'s Buy/Hold/Watch, combining
    ``fundamental_score`` with the technical entry gate; in ``gate`` mode it is
    ``Entry``/``Flat`` from the technical gate **alone**.

    ``fast=True`` computes ``add_indicators`` **once** on the full series instead
    of re-running it on each trailing slice. That is exact, not an approximation,
    for every column the scoring predicates read (``Close``, ``EMA50``, ``RSI``,
    ``RSI3``, ``ATR14``, ``MACD*``, ``VOL_SMA5/20``, ``OBV``, ``High``, ``Open``):
    each is *causal*, so its value at bar i depends only on bars <= i and a single
    pass reproduces the slice-by-slice result bit for bit. The exception is the
    envelope (``ENV_UP``/``ENV_DOWN``), whose percentiles are fitted over the whole
    window handed in — no scoring predicate reads it, but ``detail['nearest_level']``
    and any future non-causal component would be wrong, so the flag is opt-in and
    pinned by an equivalence test. Turns an O(N^2) replay into O(N): a 500-name,
    10-year universe drops from ~90 minutes to under a minute.

    The ``gate`` column is recorded in **every** mode, so a caller that wants
    plan-based exits can reuse one replay rather than paying the O(N^2) walk
    twice. ``gate`` mode exists because it is the only lookahead-free entry rule: the
    fundamentals available from yfinance are today's, so any mode that consults
    them is scoring the past with the present's information.
    """
    cols = ["tech_score", "label", "gate"]
    # NOTE: strict inequality (<=) means exactly min_bars rows returns empty;
    # min_bars+1 rows produces one entry (the first bar after the warmup window).
    if hist is None or hist.empty or "Close" not in hist or len(hist) <= min_bars:
        return pd.DataFrame(columns=cols)

    comps = TECHNICAL_COMPONENTS if components is None else components
    f = 0 if fundamental_score is None else int(fundamental_score)

    # fast: one causal pass, then slice the *enriched* frame (see the docstring).
    full = add_indicators(hist) if fast else None

    out: dict = {}
    for i in range(min_bars, len(hist)):
        enriched = full.iloc[: i + 1] if fast else add_indicators(hist.iloc[: i + 1])
        # with_levels=False: the S/R context is unscored and unread here, and it
        # dominates per-bar cost (~1.1ms vs ~0.1ms for the predicates).
        posture, tscore, detail = compute_technical_posture(
            enriched, components=comps, with_levels=False)
        gate = all(detail.get(c.name) for c in _components(comps) if c.gating)
        if mode == "composite":
            # Share the live decision rule rather than re-implementing it here —
            # a backtest of a different rule than the one that ships is worthless.
            label = decide_action(f, detail, components=comps)
        elif mode == "gate":
            label = "Entry" if gate else "Flat"
        else:
            label = posture
        out[hist.index[i]] = {"tech_score": tscore, "label": label, "gate": gate}

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


@dataclass
class PlannedTrade:
    """One entry walked to whichever of stop / target / time stop came first."""
    ticker: str
    entry_date: object
    entry: float                 # actual fill (next bar's open, plus cost)
    stop: float
    target: float
    exit_date: object
    exit_price: float            # actual fill, net of cost
    exit_reason: str             # stop | stop_gap | target | target_gap | time
    r_multiple: float            # (exit - entry) / (entry - stop)
    bars_held: int


def simulate_planned_trades(hist, entry_dates, *, ticker: str = "",
                            max_hold_bars: int = 63, cost_bps: float = 10.0,
                            slippage_mult: float = 1.0) -> list[PlannedTrade]:
    """Walk each entry to its plan's stop or target, bar by bar.

    The plan is built **point-in-time** from ``hist.iloc[:i+1]``, so the stop and
    target use only information available on the signal bar. The fill is the
    *next* bar's open (matching :func:`forward_returns`), and results are reported
    in R — ``(exit - entry) / (entry - stop)`` — which is what makes a $50 name
    and a $1,700 name comparable and is the unit expectancy is built from.

    Within one bar the **stop is checked first**: daily OHLC cannot order intrabar
    events, so we assume the worse path rather than the flattering one. A bar that
    opens beyond a level fills at the open, not the level — which is why a gap
    down can return worse than -1R.

    Entries with no usable plan (missing/zero ATR) and entries on the final bar
    (nothing to fill on) are skipped rather than raising.
    """
    if hist is None or hist.empty or not entry_dates:
        return []
    cost = cost_bps / 10_000.0 * slippage_mult
    pos = {ts: i for i, ts in enumerate(hist.index)}
    o, h, l, c = (hist[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    n = len(hist)
    # One indicator pass, sliced per entry: exact, because every column the plan
    # reads is causal (pinned by a test, like posture_timeline's fast path).
    enriched = add_indicators(hist)

    trades: list[PlannedTrade] = []
    for ts in entry_dates:
        i = pos.get(ts)
        if i is None or i + 1 >= n:
            continue                                   # no next bar to fill on
        plan = build_trade_plan(enriched.iloc[: i + 1])
        stop, target = plan["stop"], plan["target"]
        if not (np.isfinite(stop) and np.isfinite(target)):
            continue
        entry = o[i + 1] * (1 + cost)
        risk = entry - stop
        if not np.isfinite(entry) or risk <= 0:
            continue

        exit_px = exit_reason = exit_at = None
        last = min(i + max_hold_bars, n - 1)
        for j in range(i + 1, last + 1):
            if o[j] <= stop:                           # gapped through the stop
                exit_px, exit_reason = o[j], "stop_gap"
            elif l[j] <= stop:
                exit_px, exit_reason = stop, "stop"
            elif o[j] >= target:                       # gapped through the target
                exit_px, exit_reason = o[j], "target_gap"
            elif h[j] >= target:
                exit_px, exit_reason = target, "target"
            if exit_reason:
                exit_at = j
                break
        if exit_reason is None:                        # ran out of rope
            exit_at, exit_px, exit_reason = last, c[last], "time"

        net = exit_px * (1 - cost)
        trades.append(PlannedTrade(
            ticker=ticker, entry_date=ts, entry=entry, stop=stop, target=target,
            exit_date=hist.index[exit_at], exit_price=net, exit_reason=exit_reason,
            r_multiple=(net - entry) / risk, bars_held=exit_at - i,
        ))
    return trades


def aggregate_trade_stats(trades) -> dict:
    """Win rate, average win/loss and **expectancy in R** over planned trades.

    ``exit_mix`` is the diagnostic worth reading first: mostly ``time`` means the
    targets are unreachable, mostly ``stop`` means they are too tight.

    The expectancy always travels with its error bar — ``se``/``ci_lo``/``ci_hi``/
    ``p`` clustered by entry month (:func:`robustness.cluster_expectancy`), over
    ``months`` clusters. Quote the CI, not the point estimate.
    """
    rs = np.array([t.r_multiple for t in trades], dtype=float)
    mix: dict = {}
    for t in trades:
        mix[t.exit_reason] = mix.get(t.exit_reason, 0) + 1
    c = robustness.cluster_expectancy(trades)
    err = {k: c[k] for k in ("se", "ci_lo", "ci_hi", "p", "months")}
    if rs.size == 0:
        return {"n": 0, "win_rate": float("nan"), "avg_win_r": float("nan"),
                "avg_loss_r": float("nan"), "expectancy_r": float("nan"),
                "total_r": 0.0, "avg_bars_held": float("nan"), "exit_mix": mix,
                **err}
    wins, losses = rs[rs > 0], rs[rs <= 0]
    win_rate = wins.size / rs.size
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    return {
        "n": int(rs.size),
        "win_rate": float(win_rate),
        "avg_win_r": avg_win if wins.size else float("nan"),
        "avg_loss_r": avg_loss if losses.size else float("nan"),
        "expectancy_r": float(win_rate * avg_win + (1 - win_rate) * avg_loss),
        "total_r": float(rs.sum()),
        "avg_bars_held": float(np.mean([t.bars_held for t in trades])),
        "exit_mix": mix,
        **err,
    }


def random_entry_trades(prices, trades, *, reps: int = 1, seed: int = 0,
                        min_bars: int = WARMUP_BARS, **sim_kw) -> list[PlannedTrade]:
    """The random-entry null: the same plan and exits, walked from random bars.

    **Ticker-matched** — for each of ``trades`` draw ``reps`` bars from the *same*
    ticker, uniform over ``[min_bars, len-2]`` (where the gate could have fired and
    a next bar exists to fill on). Holding the names fixed isolates what the
    entry's *timing* adds. The stop/target geometry has a positive expectancy on
    its own, so this — not zero — is the bar a gate has to clear.

    ``sim_kw`` goes straight to :func:`simulate_planned_trades`, so pass the same
    ``max_hold_bars``/``cost_bps``/``slippage_mult`` as the gate's walk. Tickers
    are visited in sorted order so a seed reproduces regardless of trade order.
    """
    rng = np.random.default_rng(seed)
    counts: dict = {}
    for t in trades:
        counts[t.ticker] = counts.get(t.ticker, 0) + 1
    out: list[PlannedTrade] = []
    for tk in sorted(counts):
        hist = prices.get(tk)
        if hist is None or len(hist) - 2 < min_bars:
            continue
        pos = np.sort(rng.integers(min_bars, len(hist) - 1, size=counts[tk] * reps))
        out += simulate_planned_trades(hist, [hist.index[i] for i in pos],
                                       ticker=tk, **sim_kw)
    return out


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
    trades: list = field(default_factory=list)               # PlannedTrade, exits="plan"
    trade_stats: dict = field(default_factory=dict)          # aggregate_trade_stats
    robustness: dict = field(default_factory=dict)           # robustness.evaluate, exits="plan"
    config: dict = field(default_factory=dict)
    report_path: "str | None" = None
    excel_path: "str | None" = None


def build_results_from_prices(prices, *, mode="technical", fundamental_scores=None,
                              horizons=("1m", "3m", "6m"), max_hold="3m",
                              max_positions=10, cost_bps=10.0,
                              slippage_mult=1.0, exits="horizon",
                              fast=True, null_reps=1, null_seed=0,
                              split_at=None) -> BacktestResults:
    """Assemble a BacktestResults from an in-memory price dict (no network).

    This is the offline-testable core of :func:`run_backtest`.

    ``exits`` selects how a position is closed out. ``horizon`` (default) measures
    fixed-horizon forward returns — what the signal *led to*. ``plan`` walks every
    entry to its own :mod:`~stockanalysis.tradeplan` stop or target and reports
    R-multiples — what the strategy *would have traded*. ``plan`` always enters on
    the technical gate (``mode="gate"``), the only lookahead-free entry rule.

    ``plan`` also fills ``robustness`` (:func:`robustness.evaluate`): clustered CIs
    for the whole run and each side of ``split_at`` (default: the calendar midpoint
    of ``prices``), yearly R, and the edge over :func:`random_entry_trades` drawn
    ``null_reps`` times per trade with ``null_seed`` (``null_reps=0`` skips it).
    """
    fundamental_scores = fundamental_scores or {}
    horizons = list(horizons)
    entry_labels = ("Buy",) if mode == "composite" else ("Bullish",)
    bucket = entry_labels[0]

    timeline_map, ev_returns, base_returns, per_ticker = {}, [], [], {}
    for tk, hist in prices.items():
        tl = posture_timeline(hist, mode=mode, fast=fast,
                              fundamental_score=fundamental_scores.get(tk))
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

    trades: list = []
    evaluation: dict = {}
    if exits == "plan":
        sim_kw = dict(max_hold_bars=_bars(max_hold), cost_bps=cost_bps,
                      slippage_mult=slippage_mult)
        for tk, tl in timeline_map.items():
            # Reuse the replay above: every mode records the gate, so the entry
            # dates come free rather than costing a second O(N^2) walk.
            labelled = pd.DataFrame({"label": tl["gate"].map({True: "Entry", False: "Flat"})})
            trades += simulate_planned_trades(
                prices[tk], entry_events(labelled, ("Entry",)), ticker=tk, **sim_kw)
        null = (random_entry_trades(prices, trades, reps=null_reps, seed=null_seed,
                                    **sim_kw) if null_reps else None)
        if split_at is None:
            spans = [d for h in prices.values() if h is not None and not h.empty
                     for d in (h.index[0], h.index[-1])]
            split_at = robustness.midpoint(spans) if spans else None
        if split_at is not None:
            evaluation = robustness.evaluate(trades, null, split_at)

    return BacktestResults(
        mode=mode,
        event_stats={bucket: aggregate_event_stats(ev_all, base_all)},
        yearly=yearly_means(ev_all),
        portfolio_curve=port["curve"],
        portfolio_summary=port["summary"],
        per_ticker_returns=per_ticker,
        trades=trades,
        trade_stats=aggregate_trade_stats(trades) if exits == "plan" else {},
        robustness=evaluation,
        config={"mode": mode, "horizons": horizons, "max_hold": max_hold,
                "max_positions": max_positions, "cost_bps": cost_bps,
                "slippage_mult": slippage_mult, "entry_bucket": bucket,
                "exits": exits, "null_reps": null_reps, "null_seed": null_seed,
                "split_at": evaluation.get("split_at")},
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
                 save_report=True, exits="horizon", prices=None,
                 null_reps=1, null_seed=0, split_at=None) -> BacktestResults:
    """Network-driven entry point: fetch history, build results, write outputs.

    ``prices`` short-circuits the fetch with an already-loaded ``{ticker: frame}``
    (e.g. :func:`stockanalysis.cache.load_universe`), which is how broad-universe
    research runs fully offline.
    """
    from .ingest import load_watchlist
    from .screener import screen_fundamentals

    if prices is None:
        watchlist = config.load_watchlist_csv() if watchlist is None else watchlist
        prices, fundamentals_df = load_watchlist(watchlist, period=period)
    else:
        fundamentals_df = pd.DataFrame()      # cache holds bars only, by design

    f_scores = {}
    if mode == "composite":
        screened = screen_fundamentals(fundamentals_df)
        if not screened.empty:
            f_scores = screened["Fundamental_Score"].to_dict()

    results = build_results_from_prices(
        prices, mode=mode, fundamental_scores=f_scores, horizons=horizons,
        max_hold=max_hold, max_positions=max_positions, cost_bps=cost_bps,
        slippage_mult=slippage_mult, exits=exits,
        null_reps=null_reps, null_seed=null_seed, split_at=split_at,
    )
    results.config["period"] = period

    if benchmark and not results.portfolio_curve.empty:
        results.benchmark_curve = _benchmark_curve(benchmark, period, results.portfolio_curve)

    out = Path(out_dir) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if export_excel and (results.event_stats.get(results.config["entry_bucket"])
                         or results.trades):
        from .outputs.backtest_excel import write_backtest_workbook
        out.mkdir(parents=True, exist_ok=True)
        results.excel_path = write_backtest_workbook(results, out / "backtest.xlsx")
    if save_report and not results.portfolio_curve.empty:
        from . import charts
        out.mkdir(parents=True, exist_ok=True)
        fig = charts.build_backtest_report(results)
        results.report_path = charts.save_html(fig, out / "backtest_report.html")

    return results
