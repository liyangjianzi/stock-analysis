"""Technical indicators and window-dependent overlays.

``add_indicators`` writes a fixed set of per-bar columns that the dashboard and
signal engine read by exact name (the column contract):
``EMA20/EMA50/EMA200``, ``ENV_UP/ENV_DOWN``, ``MACD/MACD_SIG/MACD_HIST``,
``RSI``, ``RSI3``, ``ATR14``, ``VOL_SMA5``, ``VOL_SMA20``, ``OBV``, ``DVOL20``
(20-day average dollar volume, the liquidity measure). The envelope is a
*data-driven* asymmetric band around EMA20 sized so ~``envelope_coverage`` of
closes fall inside it.

There is deliberately no spread column: a close-high-low estimator (Abdi &
Ranaldo 2017) was tried and on S&P 500 bars it reads intraday volatility, not
the bid-ask bounce (MSFT ~60 bps, NCLH 0 bps, against real spreads of a few).

Trend channels and support/resistance are *window-dependent overlays* (not
per-bar columns), so they are computed on demand by ``fit_regression_channel``
and ``find_support_resistance`` and reused by both the dashboard and the scorer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator


def value_at(df: pd.DataFrame, col: str, bars_ago: int = 0) -> float:
    """Value of ``col`` at ``bars_ago`` bars before the latest bar (0 = latest,
    i.e. ``df[col].iloc[-1-bars_ago]``); ``np.nan`` if the column is missing or
    there isn't enough history.

    The single NaN-safe reader of the column contract above — both the signal
    predicates (where it implements the ``[n]`` bracket notation in their
    docstrings) and the trade plan go through it, so a change to what "missing"
    means lands in one place.
    """
    if col not in df or len(df) <= bars_ago:
        return np.nan
    return df[col].iloc[-1 - bars_ago]


def add_indicators(df: pd.DataFrame, envelope_coverage: float = 0.95,
                   envelope_fallback_pct: float = 0.025) -> pd.DataFrame:
    """Return a copy of an OHLCV DataFrame enriched with EMA/Envelope/MACD/RSI/Volume.

    Parameters
    ----------
    df : OHLCV DataFrame with a 'Close' column (and High/Low/Open/Volume).
    envelope_coverage : target fraction of closes inside the EMA20 envelope. The
        band is asymmetric — its lower/upper edges sit at the ``(1-coverage)/2`` and
        ``1-(1-coverage)/2`` percentiles of the relative deviation ``Close/EMA20-1``
        (0.95 -> 2.5th/97.5th percentiles).
    envelope_fallback_pct : symmetric half-width used when there are too few finite
        deviations (<20 bars) to estimate stable percentiles.
    """
    if df is None or df.empty or "Close" not in df:
        return df

    out = df.copy()
    close = out["Close"]

    # --- Exponential Moving Averages (trend) ---
    out["EMA20"]  = EMAIndicator(close, window=20).ema_indicator()
    out["EMA50"]  = EMAIndicator(close, window=50).ema_indicator()
    out["EMA200"] = EMAIndicator(close, window=200).ema_indicator()

    # --- EMA Envelopes: data-driven asymmetric band covering ~envelope_coverage of
    #     closes. Edges are the lower/upper percentiles of the price's relative
    #     deviation from EMA20; a constant band per series (parallel to EMA20).
    tail = (1 - envelope_coverage) / 2
    dev = (close / out["EMA20"] - 1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(dev) >= 20:
        lo, hi = dev.quantile(tail), dev.quantile(1 - tail)
    else:                                    # short series -> symmetric fallback
        lo, hi = -envelope_fallback_pct, envelope_fallback_pct
    out["ENV_UP"]  = out["EMA20"] * (1 + hi)
    out["ENV_DOWN"] = out["EMA20"] * (1 + lo)   # lo is negative

    # --- MACD (12, 26, 9): momentum via EMA differential ---
    macd = MACD(close, window_slow=26, window_fast=12, window_sign=9)
    out["MACD"]      = macd.macd()        # fast EMA - slow EMA
    out["MACD_SIG"]  = macd.macd_signal() # 9-EMA of the MACD line
    out["MACD_HIST"] = macd.macd_diff()   # MACD - signal (histogram)

    # --- RSI (14 and 3): bounded 0–100 momentum oscillators ---
    out["RSI"] = RSIIndicator(close, window=14).rsi()
    out["RSI3"] = RSIIndicator(close, window=3).rsi()

    # --- ATR (14): average true range, a volatility unit for distance checks ---
    out["ATR14"] = AverageTrueRange(out["High"], out["Low"], close, window=14).average_true_range()

    # --- Volume analysis: 5/20-day average volume + On-Balance Volume (OBV) ---
    # Degrades gracefully: if Volume is missing the columns are all-NaN, which
    # downstream code treats as "no signal" rather than crashing.
    if "Volume" in out:
        out["VOL_SMA5"] = out["Volume"].rolling(5).mean()
        out["VOL_SMA20"] = out["Volume"].rolling(20).mean()
        out["OBV"] = OnBalanceVolumeIndicator(close, out["Volume"]).on_balance_volume()
        out["DVOL20"] = (close * out["Volume"]).rolling(20).mean()
    else:
        out["VOL_SMA5"] = np.nan
        out["VOL_SMA20"] = np.nan
        out["OBV"] = np.nan
        out["DVOL20"] = np.nan

    return out


def fit_regression_channel(close: pd.Series, window: int = 90, k: float = 2.0):
    """Least-squares linear regression channel over the last ``window`` bars.

    Returns a dict aligned to the tail index of ``close``:
      index      : DatetimeIndex of the fitted window
      mid        : regression (best-fit) line
      upper/lower: parallel bands at mid ± k * std(residuals)
      slope      : per-bar slope of the mid line (sign == trend direction)
      resid_std  : standard deviation of residuals (channel half-width / k)
    Returns None if there is not enough finite data to fit a line.
    """
    if close is None:
        return None
    s = close.dropna()
    if len(s) < max(10, window // 3):
        return None
    s = s.tail(window)
    x = np.arange(len(s), dtype=float)
    y = s.to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    mid = slope * x + intercept
    resid_std = float(np.std(y - mid))
    return {
        "index": s.index,
        "mid":   pd.Series(mid, index=s.index),
        "upper": pd.Series(mid + k * resid_std, index=s.index),
        "lower": pd.Series(mid - k * resid_std, index=s.index),
        "slope": float(slope),
        "resid_std": resid_std,
    }


def swing_pivots(df: pd.DataFrame, pivot_window: int = 5) -> list[dict]:
    """Swing highs and lows, in bar order: ``{i, date, price, kind}``.

    A swing high is a bar whose High is the max within ±``pivot_window`` bars; a
    swing low is the symmetric min on Low (a bar can be both). The last
    ``pivot_window`` bars can't be pivots yet — their right side hasn't
    printed. Shared by :func:`find_support_resistance` and the pattern detector.
    """
    if df is None or df.empty or "High" not in df or "Low" not in df:
        return []
    w, n = pivot_window, len(df)
    hv, lv = df["High"].to_numpy(dtype=float), df["Low"].to_numpy(dtype=float)
    pivots = []
    for i in range(w, n - w):
        if np.isfinite(hv[i]) and hv[i] == np.nanmax(hv[i - w:i + w + 1]):
            pivots.append({"i": i, "date": df.index[i], "price": float(hv[i]), "kind": "high"})
        if np.isfinite(lv[i]) and lv[i] == np.nanmin(lv[i - w:i + w + 1]):
            pivots.append({"i": i, "date": df.index[i], "price": float(lv[i]), "kind": "low"})
    return pivots


def find_support_resistance(df: pd.DataFrame, pivot_window: int = 5,
                            cluster_tol: float = 0.015, max_levels: int = 6,
                            lookback: int = 252):
    """Detect horizontal support/resistance via swing pivots, then cluster.

    A swing-high pivot is a bar whose High is the max within ±pivot_window bars;
    a swing-low pivot is the symmetric min on Low. Pivots whose prices fall
    within ``cluster_tol`` (e.g. 1.5%) are merged into one level (mean price), and
    ``touches`` counts the members. Levels are classified support/resistance by
    their position relative to the last close. The strongest (most-touched),
    nearest levels are returned (up to ``max_levels``).

    Returns a list of {level, kind: 'support'|'resistance', touches}; [] if data
    is insufficient.
    """
    if df is None or df.empty or "High" not in df or "Low" not in df:
        return []
    d = df.tail(lookback)
    n = len(d)
    w = pivot_window
    if n < 2 * w + 1:
        return []

    # Only prices matter here: a level's kind is set by where it sits relative to
    # the last close, not by whether its pivots were highs or lows.
    prices = sorted(p["price"] for p in swing_pivots(d, pivot_window=w))
    if not prices:
        return []

    # Cluster pivots within cluster_tol of the running cluster mean.
    clusters = []  # list of dict(prices=[], mean=float)
    for price in prices:
        if clusters and abs(price - clusters[-1]["mean"]) / clusters[-1]["mean"] <= cluster_tol:
            c = clusters[-1]
            c["prices"].append(price)
            c["mean"] = float(np.mean(c["prices"]))
        else:
            clusters.append({"prices": [price], "mean": price})

    last = float(d["Close"].iloc[-1])
    levels = []
    for c in clusters:
        level = c["mean"]
        kind = "resistance" if level >= last else "support"
        levels.append({"level": level, "kind": kind, "touches": len(c["prices"])})

    # Strongest first (most touches), then nearest to the current price.
    levels.sort(key=lambda L: (-L["touches"], abs(L["level"] - last)))
    return levels[:max_levels]
