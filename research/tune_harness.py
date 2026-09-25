"""Offline tuning harness for the technical entry gate.

Vectorizes the shipped per-bar predicates so a variant can be priced in seconds
instead of minutes, then reuses the *shipped* trade simulator for exits. The
vectorization is verified bar-for-bar against ``backtest.posture_timeline`` by
``verify_equivalence.py`` -- if that check fails, nothing here is trustworthy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pathlib import Path

from stockanalysis import cache
from stockanalysis.backtest import simulate_planned_trades
from stockanalysis.indicators import add_indicators

MIN_BARS = 60          # matches posture_timeline's warmup


# --- Vectorized mirrors of signals.TECHNICAL_COMPONENTS ----------------------
# Each returns a boolean ndarray aligned to the enriched frame's index. NaN
# comparisons yield False, which is exactly what the scalar predicates do.

def v_trend_up(f, *, ema_gain=0.02, gain_bars=20):
    close, ema50 = f["Close"].to_numpy(float), f["EMA50"].to_numpy(float)
    prev = pd.Series(ema50).shift(gain_bars).to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        gain = (ema50 - prev) / prev
    ok = np.isfinite(close) & np.isfinite(ema50) & np.isfinite(prev) & (prev != 0)
    return ok & (close > ema50) & (np.nan_to_num(gain, nan=-9e9) >= ema_gain)


def v_dip_deep(f, *, rsi3_max=25.0):
    r = f["RSI3"].shift(1).to_numpy(float)
    return np.isfinite(r) & (r < rsi3_max)


def v_pullback_zone(f, *, max_atr=1.0):
    close = f["Close"].to_numpy(float)
    ema50 = f["EMA50"].to_numpy(float)
    atr = f["ATR14"].to_numpy(float)
    ok = np.isfinite(close) & np.isfinite(ema50) & np.isfinite(atr) & (atr != 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        dist = (close - ema50) / atr
    return ok & (np.nan_to_num(dist, nan=9e9) <= max_atr)


def v_turn_confirm(f):
    close = f["Close"].to_numpy(float)
    high_prev = f["High"].shift(1).to_numpy(float)
    open_ = f["Open"].to_numpy(float)
    ok = np.isfinite(close) & np.isfinite(high_prev) & np.isfinite(open_)
    return ok & (close > high_prev) & (close > open_)


def v_vol_pattern(f, *, surge=1.2):
    v5p = f["VOL_SMA5"].shift(1).to_numpy(float)
    v20 = f["VOL_SMA20"].to_numpy(float)
    vol = f["Volume"].to_numpy(float)
    ok = np.isfinite(v5p) & np.isfinite(v20) & np.isfinite(vol)
    return ok & (v5p < v20) & (vol >= surge * v20)


#: name -> (vector fn, gating?) mirroring signals.TECHNICAL_COMPONENTS
DEFAULT_SPEC = {
    "trend_up": (v_trend_up, True),
    "dip_deep": (v_dip_deep, False),
    "pullback_zone": (v_pullback_zone, True),
    "turn_confirm": (v_turn_confirm, True),
    "vol_pattern": (v_vol_pattern, False),
}


def gate_mask(f, spec=None, params=None):
    """Boolean ndarray: does every gating predicate fire on this bar?"""
    spec = DEFAULT_SPEC if spec is None else spec
    params = params or {}
    out = np.ones(len(f), dtype=bool)
    for name, (fn, gating) in spec.items():
        if not gating:
            continue
        out &= fn(f, **params.get(name, {}))
    return out


def entry_positions(mask):
    """Positions of the first bar of each True run, from MIN_BARS onward.

    Mirrors ``posture_timeline`` (which starts at MIN_BARS) followed by
    ``entry_events`` (which de-overlaps with ``fill_value=False``), so bar
    MIN_BARS itself counts as an entry when the gate is on.
    """
    if len(mask) <= MIN_BARS:
        return []
    m = mask[MIN_BARS:]
    prev = np.concatenate(([False], m[:-1]))
    return (np.flatnonzero(m & ~prev) + MIN_BARS).tolist()


ROOT = Path(__file__).resolve().parent.parent


def load_enriched(universe_csv=None, db=None, limit=None):
    """{ticker: indicator-enriched frame}, computed once and reused per variant."""
    universe_csv = universe_csv or ROOT / "data/universe_sp500.csv"
    db = db or ROOT / "data/cache/prices.db"
    tickers = pd.read_csv(universe_csv)["ticker"].tolist()
    if limit:
        tickers = tickers[:limit]
    conn = cache.connect(db)
    prices = cache.load_universe(conn, tickers)
    conn.close()
    return {tk: add_indicators(df) for tk, df in prices.items() if len(df) > MIN_BARS}


def run_variant(enriched, spec=None, params=None, *, max_hold_bars=63,
                cost_bps=10.0, slippage_mult=1.0):
    """Price one registry/parameter set. Returns a tidy per-trade DataFrame.

    Exits reuse the shipped ``simulate_planned_trades`` on the *enriched* frame:
    ``build_trade_plan`` reads only causal columns (Close, ATR14) plus an
    OHLC-based S/R fit over the slice, so slicing a single enriched pass is
    identical to re-running ``add_indicators`` per slice.
    """
    rows = []
    for tk, f in enriched.items():
        pos = entry_positions(gate_mask(f, spec, params))
        if not pos:
            continue
        dates = [f.index[i] for i in pos]
        for t in simulate_planned_trades(f, dates, ticker=tk,
                                         max_hold_bars=max_hold_bars,
                                         cost_bps=cost_bps,
                                         slippage_mult=slippage_mult):
            rows.append({"ticker": t.ticker, "entry_date": t.entry_date,
                         "r": t.r_multiple, "reason": t.exit_reason,
                         "bars": t.bars_held, "rr_planned":
                         (t.target - t.entry) / (t.entry - t.stop)})
    return pd.DataFrame(rows)


def stats(trades: pd.DataFrame) -> dict:
    """Expectancy in R with its standard error and 95% CI -- never the point
    estimate alone (see the tuning-signals skill)."""
    if trades is None or trades.empty:
        return {"n": 0, "exp_r": np.nan, "se": np.nan,
                "lo": np.nan, "hi": np.nan, "win": np.nan}
    r = trades["r"].to_numpy(float)
    n = r.size
    se = r.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
    return {"n": int(n), "exp_r": float(r.mean()), "se": float(se),
            "lo": float(r.mean() - 1.96 * se), "hi": float(r.mean() + 1.96 * se),
            "win": float((r > 0).mean()), "total_r": float(r.sum())}


def cluster_stats(trades: pd.DataFrame) -> dict:
    """Same expectancy, but with the SE computed over monthly clusters.

    7,253 trades are not 7,253 independent draws: entries cluster in time across
    correlated names, so the naive SE understates the error bar.
    """
    if trades is None or trades.empty:
        return {"clusters": 0, "se_cluster": np.nan, "lo": np.nan, "hi": np.nan}
    t = trades.copy()
    t["m"] = pd.to_datetime(t["entry_date"]).dt.to_period("M")
    g = t.groupby("m")["r"]
    means, sizes = g.mean().to_numpy(float), g.size().to_numpy(float)
    w = sizes / sizes.sum()
    mu = float((w * means).sum())
    k = len(means)
    # Weighted cluster SE: sqrt(sum w_i^2 (mean_i - mu)^2) * sqrt(k/(k-1))
    se = float(np.sqrt(((w ** 2) * (means - mu) ** 2).sum()) * np.sqrt(k / (k - 1)))
    return {"clusters": int(k), "exp_r": mu, "se_cluster": se,
            "lo": mu - 1.96 * se, "hi": mu + 1.96 * se}


def by_year(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["year"] = pd.to_datetime(t["entry_date"]).dt.year
    g = t.groupby("year")["r"]
    return pd.DataFrame({"n": g.size(), "exp_r": g.mean().round(4)})


def split(trades, train_end=2021):
    """Tune on <= train_end, confirm on > train_end."""
    y = pd.to_datetime(trades["entry_date"]).dt.year
    return trades[y <= train_end], trades[y > train_end]
