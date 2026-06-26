"""Tests for signals: compute_technical_posture + generate_signals."""
from __future__ import annotations

import pandas as pd

from stockanalysis.indicators import add_indicators
from stockanalysis.signals import compute_technical_posture, generate_signals

DETAIL_KEYS = {"above_ema50", "rsi_ok", "macd_cross_up", "trend_up",
               "vol_confirm", "nearest_level"}
OUTPUT_COLS = ["Ticker", "Sector", "Fundamental Score", "Technical Posture",
               "Tech Score", "Composite", "Final Action Signal"]


# --- compute_technical_posture -------------------------------------------------

def test_posture_uptrend_is_constructive(uptrend_ohlcv):
    label, score, detail = compute_technical_posture(add_indicators(uptrend_ohlcv))
    assert 0 <= score <= 5
    assert set(detail) == DETAIL_KEYS
    assert detail["above_ema50"] is True
    assert detail["trend_up"] is True
    assert label in {"Bullish", "Neutral"}


def test_posture_downtrend_is_weak(downtrend_ohlcv):
    label, score, detail = compute_technical_posture(add_indicators(downtrend_ohlcv))
    assert detail["above_ema50"] is False
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
