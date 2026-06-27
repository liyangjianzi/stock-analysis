"""Tests for signals: compute_technical_posture + generate_signals."""
from __future__ import annotations

import pandas as pd

from stockanalysis.indicators import add_indicators
from stockanalysis.signals import (compute_technical_posture, generate_signals,
                                    TECHNICAL_COMPONENTS, _ema50_up, _near_lower_env)

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
