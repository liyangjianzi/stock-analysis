"""One account taking the plan backtest's trades — what a trader would live through.

The plan backtest reports each trade in R, independently. But the gate fires
~2.9 times a trading day across 503 names and trades last ~8 bars, so ~23
setups are open at once; at 1% risk each, no cash account can hold them all.
:func:`simulate_account` replays the trades through one account with the trade
plan's sizing (:func:`stockanalysis.tradeplan.size_shares`), no margin by
default, and an optional cap on open risk ("heat"); entries that don't fit are
skipped and counted. :func:`drawdown_odds` bootstraps the taken trades by
calendar month to ask how deep a drawdown is likely, at several risk levels.

Conventions:

* **Closed-trade equity.** Equity moves when a trade closes; open positions are
  not marked daily, so dips *inside* a trade are missing and the drawdowns here
  are a floor, not a ceiling.
* **Same-day entries in a seeded random order.** When cash runs out mid-day,
  which setups get in must not depend on the alphabet — or on input order.
* **Nothing here is a signal.** A cap or a throttle changes which trades are
  taken or how big; whether that helps is a research question for the
  tuning-signals workflow (``research/acct_study.py``), not a default.
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from . import config
from .tradeplan import size_shares

_EPS = 1e-9


def losing_streak(rs) -> int:
    """Longest run of consecutive losing trades (R <= 0: a scratch pays no bills)."""
    best = run = 0
    for r in rs:
        run = run + 1 if r <= 0 else 0
        best = max(best, run)
    return best


def curve_stats(equity: pd.Series, start: float | None = None) -> dict:
    """Total return, CAGR and drawdowns of an equity curve (a date-indexed
    Series); ``start`` is the opening balance, by default the first value."""
    nan = float("nan")
    eq = equity.dropna()
    if eq.empty:
        return {"total_return": nan, "cagr": nan, "max_drawdown": nan,
                "current_drawdown": nan, "peak_date": None, "trough_date": None}
    total = float(eq.iloc[-1] / (float(eq.iloc[0]) if start is None else start) - 1)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    dd = eq / eq.cummax() - 1
    trough = dd.idxmin()
    return {"total_return": total,
            "cagr": float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else nan,
            "max_drawdown": float(dd.min()), "current_drawdown": float(dd.iloc[-1]),
            "peak_date": eq[:trough].idxmax(), "trough_date": trough}


def simulate_account(trades, *, start_equity: float = config.DEFAULT_ACCOUNT_SIZE,
                     risk_pct: float = config.DEFAULT_RISK_PCT,
                     max_weight: float = config.DEFAULT_MAX_WEIGHT,
                     gross_cap: float = 1.0, heat_cap: float | None = None,
                     throttle_dd: float | None = None, throttle_factor: float = 0.5,
                     seed: int = 0) -> dict:
    """Replay ``trades`` (``PlannedTrade``-like) through one account.

    Each entry is sized by :func:`stockanalysis.tradeplan.size_shares` on current
    (closed-trade) equity, and skipped when it would push invested money past
    ``gross_cap`` x equity (1.0 = no margin) or open risk past ``heat_cap`` x
    equity. With ``throttle_dd`` set, risk drops to ``throttle_factor`` x
    ``risk_pct`` while equity is that far below its peak. P&L is
    ``shares * (exit - entry)`` on the trade's own fills, so the backtest's
    costs carry through; ``ret`` is that P&L as a fraction of equity at entry.
    """
    rng = np.random.default_rng(seed)
    order = {}
    for t in trades:
        fill = pd.Timestamp(t.fill_date if getattr(t, "fill_date", None) is not None else t.entry_date)
        order.setdefault(fill, []).append(t)
    dates = sorted(set(order) | {pd.Timestamp(t.exit_date) for t in trades})

    equity = peak = float(start_equity)
    curve: dict = {}
    open_pos: list[dict] = []
    taken, skipped = [], {"cash": 0, "heat": 0, "size": 0}
    max_open = 0
    max_heat = max_gross = 0.0

    def settle(d):
        """Close every open position due by ``d``: exits free cash before entries."""
        nonlocal equity, peak
        for pos in [p for p in open_pos if p["exit_date"] <= d]:
            open_pos.remove(pos)
            equity += pos["pnl"]
            peak = max(peak, equity)
            taken.append(pos)

    for d in dates:
        settle(d)
        # Sorted first so only the seed decides who gets in, never input order.
        todays = sorted(order.get(d, []), key=lambda t: (str(t.ticker), str(t.entry_date)))
        for i in rng.permutation(len(todays)):
            t = todays[i]
            per_share = t.entry - t.stop
            if not (np.isfinite(per_share) and per_share > 0 and t.entry > 0):
                skipped["size"] += 1
                continue
            throttled = throttle_dd is not None and equity / peak - 1 <= -throttle_dd
            shares = size_shares(equity, t.entry, per_share, max_weight=max_weight,
                                 risk_pct=risk_pct * (throttle_factor if throttled else 1.0))
            if shares <= 0:
                skipped["size"] += 1
                continue
            gross = sum(p["shares"] * p["entry"] for p in open_pos) + shares * t.entry
            heat = sum(p["risk"] for p in open_pos) + shares * per_share
            if gross > gross_cap * equity + _EPS:
                skipped["cash"] += 1
            elif heat_cap is not None and heat > heat_cap * equity + _EPS:
                skipped["heat"] += 1
            else:
                pnl = shares * (t.exit_price - t.entry)
                open_pos.append({"ticker": t.ticker, "entry_date": t.entry_date, "fill_date": d,
                                 "exit_date": pd.Timestamp(t.exit_date), "entry": t.entry,
                                 "stop": t.stop, "shares": shares, "risk": shares * per_share,
                                 "pnl": pnl, "ret": pnl / equity, "r_multiple": t.r_multiple})
                max_open = max(max_open, len(open_pos))
                max_heat = max(max_heat, heat / equity)
                max_gross = max(max_gross, gross / equity)
        settle(d)                                     # in and out on the same day
        curve[d] = equity

    stats = curve_stats(pd.Series(curve, dtype=float), start=start_equity)
    taken_df = pd.DataFrame(taken)
    return {
        "equity": pd.Series(curve, dtype=float),
        "taken": taken_df,
        "skipped": skipped,
        "n_signals": len(trades),
        "n_taken": len(taken),
        **{k: stats[k] for k in ("total_return", "cagr", "max_drawdown", "peak_date", "trough_date")},
        "longest_losing_streak": losing_streak(taken_df["r_multiple"]) if taken else 0,
        "max_open_positions": max_open,
        "max_heat": max_heat,
        "max_gross": max_gross,
    }


#: simulate_account's defaults: what an account rule means when it isn't passed.
DEFAULT_RULES = {k: p.default for k, p in inspect.signature(simulate_account).parameters.items()
                 if p.default is not inspect.Parameter.empty}


def drawdown_odds(trades, *, base_risk: float = 1.0, risk_pcts=(0.005, 0.01, 0.02),
                  thresholds=(0.20, 0.30), reps: int = 2000, seed: int = 0) -> pd.DataFrame:
    """How deep a drawdown is likely, by risk per trade — a month-block bootstrap.

    ``trades`` is a sequence of ``(exit_date, ret)``, where ``ret`` is the trade's
    gain as a fraction of equity when it was taken at ``base_risk`` per trade
    (``base_risk=1.0`` reads ``ret`` as an R-multiple). Each ``risk_pct`` scales
    it by ``risk_pct / base_risk``. Pass *realized* fractions, not ``risk x R``:
    the position cap shrinks tight-stop trades, which is exactly where a gap's
    -3R lands. On the S&P 500 run ``risk x R`` put the chance of a 20%+
    drawdown at 0.5% risk at 72% against 34% realized, and the 1-in-20 worst
    drawdown at 1% risk at 66% against 52%.
    Whole calendar months are resampled with replacement (trades in one month
    share a market), each path is compounded, and its worst drawdown kept.
    Sequential compounding ignores concurrency — the per-trade view — and scaling
    ignores that caps bind more at higher risk, so the 2% row is a floor on the
    danger. Returns one row per risk level: the chance of a drawdown at least
    each threshold (``p_dd_20``...), and the median and 95th-percentile worst
    drawdown (positive fractions).
    """
    frame = pd.DataFrame(list(trades), columns=["date", "r"])
    if frame.empty:
        return pd.DataFrame()
    months = [g["r"].to_numpy(float) for _, g in frame.groupby(pd.to_datetime(frame["date"]).dt.to_period("M"))]
    rng = np.random.default_rng(seed)
    paths = [np.concatenate([months[i] for i in rng.integers(0, len(months), len(months))])
             for _ in range(reps)]
    rows = []
    for f in risk_pcts:
        worst = np.empty(reps)
        for k, rs in enumerate(paths):
            eq = np.concatenate([[1.0], np.cumprod(1 + f / base_risk * rs)])
            worst[k] = -(eq / np.maximum.accumulate(eq) - 1).min()
        row = {"risk_pct": f, "dd_median": float(np.median(worst)),
               "dd_p95": float(np.quantile(worst, 0.95))}
        row.update({f"p_dd_{round(th * 100)}": float((worst >= th).mean()) for th in thresholds})
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(trades, null_trades, params: dict, *, seeds: int = 20) -> dict:
    """The account section of a plan backtest.

    ``params`` (any :func:`simulate_account` keywords) are resolved against
    :data:`DEFAULT_RULES` and returned as ``params``, so a printer shows the
    rules that actually ran. Returns the gate's and the random entries' accounts
    under those rules, the drawdown odds of the trades the gate's account took,
    and ``spread`` — the gate account under ``seeds`` orderings of same-day
    entries, as (5th, median, 95th) percentiles. With ~23 setups open at once,
    *which* ones fit is luck: on the S&P 500 run two orderings of the same
    trades differed by ~0.5 points of CAGR and ~2.5 of worst drawdown.
    """
    rules = {**DEFAULT_RULES, **params}
    gate = simulate_account(trades, **rules)
    taken = gate["taken"]
    odds = (drawdown_odds(list(zip(taken["exit_date"], taken["ret"])), base_risk=rules["risk_pct"])
            if gate["n_taken"] else pd.DataFrame())
    spread = {}
    if seeds > 1 and trades:
        others = [k for k in range(seeds) if k != rules["seed"]][:seeds - 1]
        runs = [gate] + [simulate_account(trades, **{**rules, "seed": k}) for k in others]
        spread = {"seeds": seeds, **{
            key: tuple(float(v) for v in np.percentile([r[key] for r in runs], [5, 50, 95]))
            for key in ("cagr", "max_drawdown", "longest_losing_streak")}}
    return {"params": rules, "gate": gate,
            "null": simulate_account(null_trades, **rules) if null_trades else None,
            "odds": odds, "spread": spread}
