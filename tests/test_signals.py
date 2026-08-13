"""Tests for signals: compute_technical_posture + generate_signals."""
from __future__ import annotations

import math

import pandas as pd

from stockanalysis.indicators import add_indicators
from stockanalysis.signals import (compute_technical_posture, generate_signals,
                                    top_tickers, DEFAULT_BEAR_FRAC, DEFAULT_BULL_FRAC,
                                    TECHNICAL_COMPONENTS, _ema50_up,
                                    _near_lower_env, _posture)

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
    assert detail["above_ema50"] is True
    assert detail["trend_up"] is True
    assert detail["ema50_up"] is True
    assert label in {"Bullish", "Neutral"}


def test_posture_downtrend_is_weak(downtrend_ohlcv):
    label, score, detail = compute_technical_posture(add_indicators(downtrend_ohlcv))
    assert detail["above_ema50"] is False
    assert detail["trend_up"] is False
    assert detail["ema50_up"] is False
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


def test_ema50_up_component_directly(uptrend_ohlcv, downtrend_ohlcv):
    assert _ema50_up(add_indicators(uptrend_ohlcv)) is True
    assert _ema50_up(add_indicators(downtrend_ohlcv)) is False


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


def test_near_lower_env_component_directly():
    # Normalized band position pos = (Close - ENV_DOWN)/(ENV_UP - ENV_DOWN) <= 0.25.
    def row(close, down=10.0, up=14.0):
        return pd.DataFrame({"Close": [close], "ENV_DOWN": [down], "ENV_UP": [up]})

    assert _near_lower_env(row(10.1)) is True     # pos = 0.025 -> bottom zone
    assert _near_lower_env(row(11.0)) is True     # pos = 0.25  -> boundary (inclusive)
    assert _near_lower_env(row(13.0)) is False    # pos = 0.75  -> upper zone
    # Degenerate / missing band -> False, never raises.
    assert _near_lower_env(row(10.0, down=12.0, up=12.0)) is False  # zero-width band
    assert _near_lower_env(pd.DataFrame({"Close": [10.0]})) is False  # no ENV columns


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

def test_default_bullish_cutoff_is_five_of_seven():
    """The documented contract (CLAUDE.md, notebook §3): Bullish at >=5 of 7.

    Guards against re-tightening to 6/7: near_lower_env and above_ema50 coincide
    on ~0.5% of ticker-days, so requiring 6 caps a healthy trender at 5 and the
    posture column collapses to a constant "Neutral".
    """
    assert math.ceil(DEFAULT_BULL_FRAC * len(TECHNICAL_COMPONENTS)) == 5


def test_default_bearish_cutoff_is_two_of_seven():
    """Mirror of the Bullish contract: Bearish at <=2 of 7.

    Guards against reverting to the old hardcoded `score <= 0`, which no live
    ticker ever hit — near_lower_env and rsi_ok both fire during a decline, so
    even a straight-line crash scores 1.
    """
    assert math.floor(DEFAULT_BEAR_FRAC * len(TECHNICAL_COMPONENTS)) == 2


def test_posture_boundaries_for_the_default_registry():
    assert _posture(7, 7) == "Bullish"
    assert _posture(5, 7) == "Bullish"      # bull cutoff, inclusive
    assert _posture(4, 7) == "Neutral"
    assert _posture(3, 7) == "Neutral"      # bands do not overlap
    assert _posture(2, 7) == "Bearish"      # bear cutoff, inclusive
    assert _posture(0, 7) == "Bearish"


def test_posture_bands_rescale_with_the_component_count():
    """Both cutoffs derive from max_score, so a resized registry rescales them."""
    assert [_posture(s, 3) for s in range(4)] == ["Bearish", "Bearish", "Bullish", "Bullish"]
    assert [_posture(s, 9) for s in (0, 3, 4, 5, 6, 9)] == [
        "Bearish", "Bearish", "Neutral", "Neutral", "Bullish", "Bullish"]


def test_posture_of_an_empty_registry_is_bearish():
    """No components -> nothing can confirm; must not divide by zero."""
    assert _posture(0, 0) == "Bearish"
