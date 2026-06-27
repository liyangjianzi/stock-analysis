"""Tests for indicators: add_indicators, fit_regression_channel,
find_support_resistance."""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalysis.indicators import (
    add_indicators,
    find_support_resistance,
    fit_regression_channel,
)

CONTRACT_COLS = [
    "EMA20", "EMA50", "EMA200", "ENV_UP", "ENV_DOWN",
    "MACD", "MACD_SIG", "MACD_HIST", "RSI", "VOL_SMA20", "OBV",
]


def test_add_indicators_writes_contract_columns(uptrend_ohlcv):
    out = add_indicators(uptrend_ohlcv)
    for col in CONTRACT_COLS:
        assert col in out.columns, f"missing contract column {col}"


def test_envelope_is_data_driven_band(uptrend_ohlcv):
    out = add_indicators(uptrend_ohlcv, envelope_coverage=0.95)
    valid = out["EMA20"].notna()
    up_ratio = out.loc[valid, "ENV_UP"] / out.loc[valid, "EMA20"]
    dn_ratio = out.loc[valid, "ENV_DOWN"] / out.loc[valid, "EMA20"]
    # Global scalar band: each edge is a constant multiple of EMA20, upper >= lower.
    # (The band need not straddle EMA20 — in a trend it sits to one side.)
    np.testing.assert_allclose(up_ratio, up_ratio.iloc[0])
    np.testing.assert_allclose(dn_ratio, dn_ratio.iloc[0])
    assert (out.loc[valid, "ENV_UP"] >= out.loc[valid, "ENV_DOWN"]).all()
    # ~95% of closes fall inside the band (the coverage target, measured in-sample).
    inside = ((out.loc[valid, "Close"] >= out.loc[valid, "ENV_DOWN"]) &
              (out.loc[valid, "Close"] <= out.loc[valid, "ENV_UP"]))
    assert 0.92 <= inside.mean() <= 0.98


def test_envelope_short_series_uses_fallback():
    # Fewer than 20 finite deviations -> symmetric ±envelope_fallback_pct band.
    n = 30
    base = np.linspace(10.0, 11.0, n)
    df = pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Volume": np.full(n, 1_000_000.0),
    })
    out = add_indicators(df, envelope_fallback_pct=0.03)
    valid = out["EMA20"].notna()
    assert valid.any()
    np.testing.assert_allclose(out.loc[valid, "ENV_UP"], out.loc[valid, "EMA20"] * 1.03)
    np.testing.assert_allclose(out.loc[valid, "ENV_DOWN"], out.loc[valid, "EMA20"] * 0.97)


def test_rsi_bounded_0_100(uptrend_ohlcv):
    rsi = add_indicators(uptrend_ohlcv)["RSI"].dropna()
    assert ((rsi >= 0) & (rsi <= 100)).all()


def test_add_indicators_does_not_mutate_input(uptrend_ohlcv):
    before = uptrend_ohlcv.copy()
    add_indicators(uptrend_ohlcv)
    pd.testing.assert_frame_equal(uptrend_ohlcv, before)


def test_volume_columns_degrade_when_volume_missing(uptrend_ohlcv):
    no_vol = uptrend_ohlcv.drop(columns=["Volume"])
    out = add_indicators(no_vol)  # must not raise
    assert out["VOL_SMA20"].isna().all()
    assert out["OBV"].isna().all()


def test_regression_slope_sign_matches_trend(uptrend_ohlcv, downtrend_ohlcv):
    up = fit_regression_channel(uptrend_ohlcv["Close"])
    down = fit_regression_channel(downtrend_ohlcv["Close"])
    assert up is not None and up["slope"] > 0
    assert down is not None and down["slope"] < 0
    assert set(up) == {"index", "mid", "upper", "lower", "slope", "resid_std"}
    # upper band sits above lower band everywhere
    assert (up["upper"] >= up["lower"]).all()


def test_regression_returns_none_when_too_few_points():
    short = pd.Series(np.arange(5, dtype=float))  # < max(10, window//3)
    assert fit_regression_channel(short) is None


def test_support_resistance_structure(uptrend_ohlcv):
    levels = find_support_resistance(uptrend_ohlcv)
    assert isinstance(levels, list)
    assert len(levels) <= 6  # max_levels default
    for L in levels:
        assert set(L) == {"level", "kind", "touches"}
        assert L["kind"] in {"support", "resistance"}
        assert L["touches"] >= 1


def test_support_resistance_empty_when_insufficient_rows():
    tiny = pd.DataFrame({
        "High": [1.0, 2.0, 3.0], "Low": [0.5, 1.5, 2.5], "Close": [1.0, 2.0, 3.0],
    })  # n < 2*pivot_window + 1
    assert find_support_resistance(tiny) == []
