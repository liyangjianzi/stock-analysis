"""Shared pytest fixtures: deterministic synthetic data, no network.

Everything here is built with a fixed numpy seed so the indicator/signal tests
are reproducible and never flake. No yfinance / live I/O is involved.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(closes: np.ndarray, start: str = "2023-01-02") -> pd.DataFrame:
    """Assemble a daily OHLCV frame from a close-price path.

    Open/High/Low are derived from Close with small fixed offsets so High >=
    {Open, Close} >= Low always holds. Volume is a deterministic positive series.
    """
    n = len(closes)
    idx = pd.bdate_range(start=start, periods=n)
    close = pd.Series(closes, index=idx)
    openp = close.shift(1).fillna(close.iloc[0])
    high = np.maximum(openp, close) * 1.01
    low = np.minimum(openp, close) * 0.99
    volume = pd.Series(1_000_000 + (np.arange(n) % 7) * 50_000, index=idx)
    return pd.DataFrame(
        {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": volume}
    )


@pytest.fixture
def uptrend_ohlcv() -> pd.DataFrame:
    """~260 bars trending up with mild deterministic noise (enough for EMA200
    and a 90-bar regression channel)."""
    n = 260
    base = np.linspace(100.0, 200.0, n)            # steady uptrend
    noise = np.sin(np.arange(n) / 5.0) * 2.0       # deterministic wobble
    return _make_ohlcv(base + noise)


@pytest.fixture
def downtrend_ohlcv() -> pd.DataFrame:
    """~260 bars trending down — drives a Bearish/low technical posture."""
    n = 260
    base = np.linspace(200.0, 100.0, n)
    noise = np.sin(np.arange(n) / 5.0) * 2.0
    return _make_ohlcv(base + noise)


@pytest.fixture
def make_screened():
    """Factory for a screened frame (indexed by Ticker, with Sector +
    Fundamental_Score) — the shape generate_signals and the exporter consume.

    Usage: ``make_screened({"AAPL": 6, "JPM": 3})``.
    """
    def _make(scores: dict[str, int]) -> pd.DataFrame:
        df = pd.DataFrame(
            {"Fundamental_Score": list(scores.values()),
             "Sector": ["Technology"] * len(scores)},
            index=list(scores),
        )
        df.index.name = "Ticker"
        return df
    return _make


@pytest.fixture
def fundamentals_df() -> pd.DataFrame:
    """Fundamentals frame indexed by ticker covering the screener's columns.

    GOOD passes all six thresholds, BAD fails all six, and NAN is entirely
    missing (asserts the NaN-means-fail contract).
    """
    data = {
        "GOOD": {"Sector": "Technology", "PE": 18.0, "EPS_Growth": 0.20,
                 "Rev_Growth": 0.15, "Debt_Equity": 0.5, "Div_Yield": 0.03,
                 "FCF": 1.0e9},
        "BAD":  {"Sector": "Technology", "PE": 40.0, "EPS_Growth": 0.01,
                 "Rev_Growth": 0.01, "Debt_Equity": 2.0, "Div_Yield": 0.0,
                 "FCF": -1.0e9},
        "NAN":  {"Sector": "Healthcare", "PE": np.nan, "EPS_Growth": np.nan,
                 "Rev_Growth": np.nan, "Debt_Equity": np.nan, "Div_Yield": np.nan,
                 "FCF": np.nan},
    }
    df = pd.DataFrame.from_dict(data, orient="index")
    df.index.name = "Ticker"
    return df
