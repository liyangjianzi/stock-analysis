"""Shared inference for the research studies: a Newey-West mean and period windows.

``pit_study`` and ``mom_study`` both summarise a time series of monthly results
as a mean with an autocorrelation-robust error bar, over the whole span and over
sub-periods. One copy lives here so the two studies can't drift apart.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def nw(x, lags: int = 3) -> dict:
    """Mean of a monthly series with a Newey-West (Bartlett) SE, 95% CI,
    two-sided p, and the annualized information ratio. Fewer than 3 points
    leave everything NaN."""
    x = pd.Series(x, dtype=float).dropna().to_numpy()
    T = len(x)
    if T < 3:
        return dict(mean=np.nan, se=np.nan, lo=np.nan, hi=np.nan, p=np.nan, T=T, ir=np.nan)
    mu, e = x.mean(), x - x.mean()
    var = e @ e / T
    for k in range(1, min(lags, T - 1) + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / T
    se = math.sqrt(var / T)
    p = math.erfc(abs(mu / se) / math.sqrt(2)) if se > 0 else np.nan
    sd = x.std(ddof=1)
    return dict(mean=mu, se=se, lo=mu - 1.96 * se, hi=mu + 1.96 * se, p=p, T=T,
                ir=mu / sd * math.sqrt(12) if sd > 0 else np.nan)


def window(dates, lo=None, hi=None) -> np.ndarray:
    """Boolean mask for ``lo <= date < hi``; a None bound is open."""
    d = pd.DatetimeIndex(dates)
    m = np.ones(len(d), dtype=bool)
    if lo is not None:
        m &= d >= pd.Timestamp(lo)
    if hi is not None:
        m &= d < pd.Timestamp(hi)
    return m


def by_period(series: pd.Series, periods, lags: int = 3) -> dict:
    """``nw`` over the whole series plus each ``(name, lo, hi)`` window."""
    out = {"all": nw(series, lags)}
    for name, lo, hi in periods:
        out[name] = nw(series[window(series.index, lo, hi)], lags)
    return out
