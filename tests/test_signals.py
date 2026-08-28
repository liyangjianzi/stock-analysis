"""Tests for signals: compute_technical_posture + generate_signals."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from stockanalysis.indicators import add_indicators
from stockanalysis.signals import (compute_technical_posture, generate_signals,
                                    top_tickers, DEFAULT_BEAR_FRAC, DEFAULT_BULL_FRAC,
                                    TECHNICAL_COMPONENTS, _dip_deep, _pullback_zone,
                                    _trend_up, _turn_confirm, _vol_pattern, _posture)

DETAIL_KEYS = {name for name, _ in TECHNICAL_COMPONENTS} | {"nearest_level"}
MAX_TECH = len(TECHNICAL_COMPONENTS)
OUTPUT_COLS = ["Ticker", "Sector", "Fundamental Score", "Technical Posture",
               "Tech Score", "Composite", "Final Action Signal"]

# Trivial predicates for exercising the configurable component registry.
ALWAYS = ("always", lambda df: True)
NEVER = ("never", lambda df: False)


# --- compute_technical_posture -------------------------------------------------

def test_posture_uptrend_is_constructive(uptrend_ohlcv):
    label, score, detail = compute_technical_posture(add_indicators(uptrend_ohlcv))
    assert 0 <= score <= MAX_TECH
    assert set(detail) == DETAIL_KEYS
    assert detail["trend_up"] is True


def test_posture_downtrend_is_weak(downtrend_ohlcv):
    label, score, detail = compute_technical_posture(add_indicators(downtrend_ohlcv))
    assert detail["trend_up"] is False
    assert label in {"Bearish", "Neutral"}


def test_posture_empty_df_is_bearish_zero():
    label, score, detail = compute_technical_posture(pd.DataFrame())
    assert (label, score) == ("Bearish", 0)
    assert set(detail) == DETAIL_KEYS


# --- generate_signals: fusion math --------------------------------------------
# With an empty tech_data dict every ticker's tech score is 0 (posture None ->
# Bearish/0), so composite == 0.70*(f/6) and the action thresholds are exact.

def test_composite_formula_and_actions(make_screened):
    screened = make_screened({"BUY": 6, "HOLD": 5, "WATCH": 3})
    out = generate_signals(screened, tech_data={})

    by_ticker = out.set_index("Ticker")
    # composite == 0.70 * f/6  (tech score 0)
    assert by_ticker.loc["BUY", "Composite"] == round(0.70 * 6 / 6, 3)      # 0.700
    assert by_ticker.loc["HOLD", "Composite"] == round(0.70 * 5 / 6, 3)     # 0.583
    assert by_ticker.loc["WATCH", "Composite"] == round(0.70 * 3 / 6, 3)    # 0.350

    assert by_ticker.loc["BUY", "Final Action Signal"] == "Buy"       # >= 0.60
    assert by_ticker.loc["HOLD", "Final Action Signal"] == "Hold"     # >= 0.40
    assert by_ticker.loc["WATCH", "Final Action Signal"] == "Watch"   # < 0.40


def test_output_columns_and_ordering(make_screened):
    screened = make_screened({"WATCH": 3, "BUY": 6, "HOLD": 5})
    out = generate_signals(screened, tech_data={})
    assert list(out.columns) == OUTPUT_COLS
    # ranked Buy -> Hold -> Watch regardless of input order
    assert out["Final Action Signal"].tolist() == ["Buy", "Hold", "Watch"]


def test_empty_screened_returns_empty():
    assert generate_signals(pd.DataFrame(), tech_data={}).empty
    assert generate_signals(None, tech_data={}).empty


def test_trend_up_component_directly():
    # Close > EMA50 AND (EMA50 - EMA50[20]) / EMA50[20] >= 2%.
    def row(close, ema50_now, ema50_20ago):
        ema50 = [ema50_20ago] + [np.nan] * 19 + [ema50_now]
        return pd.DataFrame({"Close": [np.nan] * 19 + [np.nan, close], "EMA50": ema50})

    assert _trend_up(row(110.0, 105.0, 100.0)) is True     # above EMA50, +5% over 20 bars
    assert _trend_up(row(95.0, 105.0, 100.0)) is False      # below EMA50
    assert _trend_up(row(110.0, 101.0, 100.0)) is False     # above EMA50 but only +1%


def test_dip_deep_component_directly():
    # RSI(3)[1] < 25 -- yesterday's RSI(3), not today's.
    assert _dip_deep(pd.DataFrame({"RSI3": [10.0, 50.0]})) is True
    assert _dip_deep(pd.DataFrame({"RSI3": [50.0, 10.0]})) is False
    assert _dip_deep(pd.DataFrame({"RSI3": [10.0]})) is False   # no prior bar


def test_pullback_zone_component_directly():
    # (Close - EMA50) / ATR14 <= 1.0.
    def row(close, ema50, atr14):
        return pd.DataFrame({"Close": [close], "EMA50": [ema50], "ATR14": [atr14]})

    assert _pullback_zone(row(105.0, 100.0, 10.0)) is True    # 0.5 <= 1.0
    assert _pullback_zone(row(115.0, 100.0, 10.0)) is False   # 1.5 > 1.0
    assert _pullback_zone(row(105.0, 100.0, 0.0)) is False    # zero ATR -> no crash


def test_turn_confirm_component_directly():
    # Close > High[1] AND Close > Open.
    def row(close, high_prev, open_):
        return pd.DataFrame({"Close": [np.nan, close], "High": [high_prev, np.nan],
                              "Open": [np.nan, open_]})

    assert _turn_confirm(row(105.0, 100.0, 102.0)) is True
    assert _turn_confirm(row(99.0, 100.0, 98.0)) is False    # didn't clear prior high
    assert _turn_confirm(row(105.0, 100.0, 106.0)) is False  # closed red
    assert _turn_confirm(pd.DataFrame({"Close": [105.0]})) is False  # no prior bar


def test_vol_pattern_component_directly():
    # SMA(Volume,5)[1] < VOL_SMA20 AND Volume >= 1.2 * VOL_SMA20.
    def row(vol_sma5_prev, vol_sma20, volume):
        return pd.DataFrame({"VOL_SMA5": [vol_sma5_prev, np.nan],
                              "VOL_SMA20": [np.nan, vol_sma20],
                              "Volume": [np.nan, volume]})

    assert _vol_pattern(row(800_000, 1_000_000, 1_300_000)) is True
    assert _vol_pattern(row(800_000, 1_000_000, 1_100_000)) is False   # volume too low
    assert _vol_pattern(row(1_100_000, 1_000_000, 1_300_000)) is False  # quiet spell missing


def test_components_override_scales_max_and_posture(uptrend_ohlcv):
    enriched = add_indicators(uptrend_ohlcv)
    one = [ALWAYS]
    label, score, detail = compute_technical_posture(enriched, components=one)
    assert score == 1
    assert set(detail) == {"always", "nearest_level"}
    assert label == "Bullish"        # 1 >= ceil(2/3 * 1) == 1

    two = [ALWAYS, NEVER]
    label2, score2, _ = compute_technical_posture(enriched, components=two)
    assert score2 == 1               # max 2
    assert label2 == "Neutral"       # 0 < 1 < ceil(2/3*2)=2


# --- top_tickers ---------------------------------------------------------------

def test_top_tickers_takes_the_head_of_the_ranked_matrix(uptrend_ohlcv, downtrend_ohlcv,
                                                         make_screened):
    """The matrix is pre-ranked, so head(n) is the top-pick list."""
    screened = make_screened({"UP": 6, "MID": 3, "DOWN": 0})
    tech = {"UP": add_indicators(uptrend_ohlcv),
            "MID": add_indicators(uptrend_ohlcv),
            "DOWN": add_indicators(downtrend_ohlcv)}
    matrix = generate_signals(screened, tech)

    assert top_tickers(matrix, 2) == matrix["Ticker"].tolist()[:2]
    assert top_tickers(matrix) == matrix["Ticker"].tolist()      # None -> all
    assert top_tickers(matrix, 99) == matrix["Ticker"].tolist()  # n > len -> all


def test_top_tickers_on_empty_matrix_returns_empty_list():
    """generate_signals returns a column-less frame when nothing is screened,
    so top_tickers must not index into it."""
    assert top_tickers(pd.DataFrame(), 5) == []
    assert top_tickers(None, 5) == []


# --- posture cutoff ------------------------------------------------------------

def test_default_bullish_cutoff_is_four_of_five():
    """The documented contract (CLAUDE.md, notebook §3): Bullish at >=4 of 5."""
    assert math.ceil(DEFAULT_BULL_FRAC * len(TECHNICAL_COMPONENTS)) == 4


def test_default_bearish_cutoff_is_one_of_five():
    """Mirror of the Bullish contract: Bearish at <=1 of 5."""
    assert math.floor(DEFAULT_BEAR_FRAC * len(TECHNICAL_COMPONENTS)) == 1


def test_posture_boundaries_for_the_default_registry():
    assert _posture(5, 5) == "Bullish"
    assert _posture(4, 5) == "Bullish"      # bull cutoff, inclusive
    assert _posture(2, 5) == "Neutral"
    assert _posture(1, 5) == "Bearish"      # bear cutoff, inclusive
    assert _posture(0, 5) == "Bearish"


def test_posture_bands_rescale_with_the_component_count():
    """Both cutoffs derive from max_score, so a resized registry rescales them."""
    assert [_posture(s, 3) for s in range(4)] == ["Bearish", "Bearish", "Bullish", "Bullish"]
    assert [_posture(s, 9) for s in (0, 3, 4, 5, 6, 9)] == [
        "Bearish", "Bearish", "Neutral", "Neutral", "Bullish", "Bullish"]


def test_posture_of_an_empty_registry_is_bearish():
    """No components -> nothing can confirm; must not divide by zero."""
    assert _posture(0, 0) == "Bearish"
