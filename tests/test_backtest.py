from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalysis.backtest import posture_timeline, entry_events, forward_returns, aggregate_event_stats, yearly_means, simulate_portfolio
from stockanalysis.indicators import add_indicators
from stockanalysis.signals import compute_technical_posture, TECHNICAL_COMPONENTS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(closes, start: str = "2023-01-02") -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a close-price array."""
    import numpy as np
    closes = pd.Series(closes)
    n = len(closes)
    idx = pd.bdate_range(start=start, periods=n)
    close = pd.Series(closes.values, index=idx)
    openp = close.shift(1).fillna(close.iloc[0])
    high = pd.Series(__import__("numpy").maximum(openp, close) * 1.01, index=idx)
    low = pd.Series(__import__("numpy").minimum(openp, close) * 0.99, index=idx)
    volume = pd.Series(1_000_000 + (pd.RangeIndex(n) % 7) * 50_000, index=idx)
    return pd.DataFrame(
        {"Open": openp, "High": high, "Low": low, "Close": close, "Volume": volume}
    )


def _spike_ohlcv() -> pd.DataFrame:
    """220-bar series: first 180 bars are a gentle uptrend, last 40 bars spike
    sharply upward (5× the prior range).  This makes the full-series envelope
    percentiles (ENV_UP / ENV_DOWN) clearly wider than the truncated-series
    percentiles, so a naïve single-pass implementation that runs add_indicators
    once on the full frame will compute different ENV values for early rows.
    """
    import numpy as np
    n_pre, n_post = 180, 40
    pre = np.linspace(100.0, 110.0, n_pre)           # gentle +10 % ramp
    post = np.linspace(110.0, 300.0, n_post)          # sharp spike (+170 %)
    closes = np.concatenate([pre, post])
    return _make_ohlcv(closes)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_posture_timeline_is_point_in_time():
    """Truncating future bars must not change any past label/score.

    The fixture has a dramatic spike in the last 40 bars.  A naïve impl that
    runs add_indicators *once* on the full series produces very different
    ENV_UP/ENV_DOWN percentiles (the spike inflates the upper deviation) and
    therefore different `near_lower_env` evaluations for early rows.  The real
    point-in-time impl re-runs add_indicators on each trailing prefix so the
    overlap region must match exactly.
    """
    hist = _spike_ohlcv()
    cut = 180   # exactly where the spike begins
    full_tl = posture_timeline(hist, min_bars=60)
    trunc_tl = posture_timeline(hist.iloc[:cut], min_bars=60)

    assert not trunc_tl.empty, "truncated timeline must not be empty"
    common = trunc_tl.index
    pd.testing.assert_frame_equal(full_tl.loc[common], trunc_tl.loc[common])


def test_posture_timeline_point_in_time_independent_check():
    """Direct, independent point-in-time check for an early date.

    Picks an early bar ``d`` in the stable (pre-spike) portion and asserts
    that posture_timeline's value for ``d`` exactly matches an independently
    computed posture using only data up-to-and-including ``d``.  A naïve
    single-pass implementation would fail this because the full-series envelope
    percentiles (inflated by the later spike) differ from the prefix-only ones.
    """
    hist = _spike_ohlcv()
    tl = posture_timeline(hist, min_bars=60)

    # Pick a date well inside the pre-spike region (bar 80, 0-indexed).
    # min_bars=60 so the first timeline entry is bar index 60; pick bar 80.
    probe_date = hist.index[80]
    assert probe_date in tl.index, "probe_date must be in the timeline"

    # Independently compute posture from only the prefix ending at probe_date.
    prefix = hist.loc[:probe_date]
    enriched = add_indicators(prefix)
    ref_label, ref_score, _ = compute_technical_posture(enriched, components=TECHNICAL_COMPONENTS)

    row = tl.loc[probe_date]
    assert row["tech_score"] == ref_score, (
        f"tech_score mismatch at {probe_date}: "
        f"timeline={row['tech_score']} vs independent={ref_score}"
    )
    assert row["label"] == ref_label, (
        f"label mismatch at {probe_date}: "
        f"timeline={row['label']!r} vs independent={ref_label!r}"
    )


def test_posture_timeline_labels_are_technical():
    tl = posture_timeline(_spike_ohlcv(), min_bars=60)
    assert set(tl.columns) == {"tech_score", "label"}
    assert set(tl["label"]).issubset({"Bearish", "Neutral", "Bullish"})


def test_posture_timeline_composite_uses_fundamentals(uptrend_ohlcv):
    low = posture_timeline(uptrend_ohlcv, mode="composite", fundamental_score=0)
    high = posture_timeline(uptrend_ohlcv, mode="composite", fundamental_score=6)

    assert set(low["label"]).issubset({"Buy", "Hold", "Watch"})
    # With a strong uptrend, raising the fundamental score can only push the
    # composite up, so the count of "Buy" labels must be >= the low-score count.
    assert (high["label"] == "Buy").sum() >= (low["label"] == "Buy").sum()


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
