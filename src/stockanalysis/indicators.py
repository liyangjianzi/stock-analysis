"""Technical indicators and window-dependent overlays.

``add_indicators`` writes a fixed set of per-bar columns that the dashboard and
signal engine read by exact name (the column contract):
``EMA20/EMA50/EMA200``, ``ENV_UP/ENV_DOWN``, ``MACD/MACD_SIG/MACD_HIST``,
``RSI``, ``VOL_SMA20``, ``OBV``.

Trend channels and support/resistance are *window-dependent overlays* (not
per-bar columns), so they are computed on demand by ``fit_regression_channel``
and ``find_support_resistance`` and reused by both the dashboard and the scorer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volume import OnBalanceVolumeIndicator


def add_indicators(df: pd.DataFrame, envelope_pct: float = 0.025) -> pd.DataFrame:
    """Return a copy of an OHLCV DataFrame enriched with EMA/Envelope/MACD/RSI/Volume.

    Parameters
    ----------
    df : OHLCV DataFrame with a 'Close' column (and High/Low/Open/Volume).
    envelope_pct : half-width of the EMA20 envelope band (0.025 == ±2.5%).
    """
    if df is None or df.empty or "Close" not in df:
        return df

    out = df.copy()
    close = out["Close"]

    # --- Exponential Moving Averages (trend) ---
    out["EMA20"]  = EMAIndicator(close, window=20).ema_indicator()
    out["EMA50"]  = EMAIndicator(close, window=50).ema_indicator()
    out["EMA200"] = EMAIndicator(close, window=200).ema_indicator()

    # --- EMA Envelopes: ±envelope_pct band around the 20-day EMA ---
    out["ENV_UP"]  = out["EMA20"] * (1 + envelope_pct)
    out["ENV_DOWN"] = out["EMA20"] * (1 - envelope_pct)

    # --- MACD (12, 26, 9): momentum via EMA differential ---
    macd = MACD(close, window_slow=26, window_fast=12, window_sign=9)
    out["MACD"]      = macd.macd()        # fast EMA - slow EMA
    out["MACD_SIG"]  = macd.macd_signal() # 9-EMA of the MACD line
    out["MACD_HIST"] = macd.macd_diff()   # MACD - signal (histogram)

    # --- RSI (14): bounded 0–100 momentum oscillator ---
    out["RSI"] = RSIIndicator(close, window=14).rsi()

    # --- Volume analysis: 20-day average volume + On-Balance Volume (OBV) ---
    # Degrades gracefully: if Volume is missing the columns are all-NaN, which
    # downstream code treats as "no signal" rather than crashing.
    if "Volume" in out:
        out["VOL_SMA20"] = out["Volume"].rolling(20).mean()
        out["OBV"] = OnBalanceVolumeIndicator(close, out["Volume"]).on_balance_volume()
    else:
        out["VOL_SMA20"] = np.nan
        out["OBV"] = np.nan

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

    hv, lv = d["High"].to_numpy(), d["Low"].to_numpy()
    pivots = []  # (price, kind)
    for i in range(w, n - w):
        win_hi = hv[i - w:i + w + 1]
        win_lo = lv[i - w:i + w + 1]
        if np.isfinite(hv[i]) and hv[i] == np.nanmax(win_hi):
            pivots.append((float(hv[i]), "resistance"))
        if np.isfinite(lv[i]) and lv[i] == np.nanmin(win_lo):
            pivots.append((float(lv[i]), "support"))
    if not pivots:
        return []

    # Cluster pivots within cluster_tol of the running cluster mean.
    pivots.sort(key=lambda p: p[0])
    clusters = []  # list of dict(prices=[], mean=float)
    for price, _kind in pivots:
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
