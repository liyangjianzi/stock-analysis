"""Tests for signals: compute_technical_posture + generate_signals."""
from __future__ import annotations

import math

import numpy as np
import pytest
import pandas as pd

from conftest import pullback_ohlcv

from stockanalysis.indicators import add_indicators
from stockanalysis.signals import (compute_technical_posture, decide_action,
                                    generate_signals, top_tickers, DEFAULT_BEAR_FRAC,
                                    DEFAULT_BULL_FRAC, DEFAULT_FUND_MIN,
                                    GATE_COMPONENTS, TECHNICAL_COMPONENTS,
                                    _dip_deep, _pullback_zone, _trend_up,
                                    _turn_confirm, _vol_pattern, _posture)
from stockanalysis.tradeplan import MATRIX_COLUMNS

DETAIL_KEYS = {c.name for c in TECHNICAL_COMPONENTS} | {"nearest_level"}
MAX_TECH = len(TECHNICAL_COMPONENTS)
OUTPUT_COLS = (["Ticker", "Sector", "Fundamental Score", "Technical Posture",
                "Tech Score", "Composite", "Final Action Signal"]
               + list(MATRIX_COLUMNS.values()))

GATE_PASSES = dict.fromkeys(GATE_COMPONENTS, True)
GATE_FAILS = {**GATE_PASSES, GATE_COMPONENTS[-1]: False}

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


# --- decide_action: quality test + technical gate -------------------------------

def test_buy_needs_both_quality_and_the_gate():
    assert decide_action(6, GATE_PASSES, fund_min=4) == "Buy"
    assert decide_action(4, GATE_PASSES, fund_min=4) == "Buy"      # cutoff inclusive
    assert decide_action(6, GATE_FAILS, fund_min=4) == "Hold"      # ownable, not timed
    assert decide_action(3, GATE_PASSES, fund_min=4) == "Watch"    # setup without quality
    assert decide_action(3, GATE_FAILS, fund_min=4) == "Watch"


def test_perfect_fundamentals_without_a_setup_are_not_a_buy():
    """The regression this design exists to prevent.

    Under the old weighted composite, fund=6 / tech=0 scored 0.70*(6/6) = 0.700
    and cleared the 0.60 Buy bar with no technical confirmation whatsoever.
    """
    no_setup = {c.name: False for c in TECHNICAL_COMPONENTS}
    assert decide_action(6, no_setup) == "Hold"


def test_non_gate_components_do_not_block_a_buy():
    """dip_deep and vol_pattern are scored but never blocking."""
    detail = {**{c.name: False for c in TECHNICAL_COMPONENTS}, **GATE_PASSES}
    assert set(detail) - set(GATE_COMPONENTS), "registry should have non-gate components"
    assert decide_action(5, detail) == "Buy"


def test_a_custom_registry_gates_on_its_own_components():
    """The gate is a property of the registry, so a custom one cannot silently
    fall back to the default gate (which would collapse to no gate at all)."""
    from stockanalysis.signals import TechnicalComponent
    custom = [TechnicalComponent("always", lambda df: True, gating=True),
              TechnicalComponent("never", lambda df: False, gating=True)]
    assert decide_action(6, {"always": True, "never": True}, components=custom) == "Buy"
    assert decide_action(6, {"always": True, "never": False}, components=custom) == "Hold"
    # None of the *default* gate names appear, yet the custom gate still binds.
    assert not set(GATE_COMPONENTS) & {"always", "never"}


def test_plain_tuples_are_accepted_as_a_registry_and_are_non_gating():
    """Ad-hoc (name, predicate) tuples still work; nothing gates, so quality alone
    decides — the caller opted out of a gate by not declaring one."""
    assert decide_action(6, {"always": False}, components=[ALWAYS]) == "Buy"


def test_gate_components_is_derived_from_the_registry():
    assert GATE_COMPONENTS == tuple(c.name for c in TECHNICAL_COMPONENTS if c.gating)


def test_default_fund_min_is_four_of_six():
    assert DEFAULT_FUND_MIN == 4


# --- generate_signals ----------------------------------------------------------

def test_composite_is_carried_as_a_ranking_key_not_the_action(make_screened):
    """With no tech data every gate component is False, so nothing can be a Buy
    however strong the fundamentals — but Composite still ranks the names."""
    screened = make_screened({"STRONG": 6, "MID": 5, "WEAK": 3})
    out = generate_signals(screened, tech_data={}).set_index("Ticker")

    assert out.loc["STRONG", "Composite"] == round(0.70 * 6 / 6, 3)      # 0.700
    assert out.loc["MID", "Composite"] == round(0.70 * 5 / 6, 3)         # 0.583
    assert out.loc["WEAK", "Composite"] == round(0.70 * 3 / 6, 3)        # 0.350

    assert out.loc["STRONG", "Final Action Signal"] == "Hold"
    assert out.loc["MID", "Final Action Signal"] == "Hold"
    assert out.loc["WEAK", "Final Action Signal"] == "Watch"             # below fund_min


def test_a_real_pullback_setup_produces_a_buy(make_screened, setup_frame):
    """End-to-end: the gate fires on synthetic OHLCV, not just on a hand-built
    detail dict."""
    tech = {"SETUP": setup_frame}
    out = generate_signals(make_screened({"SETUP": 6}), tech).set_index("Ticker")
    assert out.loc["SETUP", "Final Action Signal"] == "Buy"


def test_a_setup_that_misses_one_gate_component_is_a_hold(make_screened):
    """Same series, rebounding too far to stay in the pullback zone."""
    tech = {"EXTENDED": add_indicators(pullback_ohlcv(rebound=0.06))}
    out = generate_signals(make_screened({"EXTENDED": 6}), tech).set_index("Ticker")
    assert out.loc["EXTENDED", "Final Action Signal"] == "Hold"


def test_fund_min_is_configurable(make_screened, setup_frame):
    tech = {"SETUP": setup_frame}
    screened = make_screened({"SETUP": 4})
    assert generate_signals(screened, tech)["Final Action Signal"][0] == "Buy"
    assert generate_signals(screened, tech, fund_min=5)["Final Action Signal"][0] == "Watch"


# --- generate_signals: the attached trade plan ---------------------------------

def test_buy_rows_carry_a_priced_trade_plan(make_screened, setup_frame):
    tech = {"SETUP": setup_frame}
    row = generate_signals(make_screened({"SETUP": 6}), tech,
                           account_size=50_000, risk_pct=0.01).iloc[0]

    assert row["Stop"] < row["Entry"] < row["Target"]
    assert row["Shares"] > 0
    assert row["Risk $"] <= 50_000 * 0.01
    assert row["R:R"] > 0
    assert row["Stop Basis"] in {"structure", "atr"}


def test_watch_rows_carry_no_plan(make_screened, setup_frame):
    """Watch names are not entry candidates, so they get an empty plan (which
    also skips the support/resistance fit for every rejected name)."""
    tech = {"SETUP": setup_frame}
    row = generate_signals(make_screened({"SETUP": 2}), tech).iloc[0]
    assert row["Final Action Signal"] == "Watch"
    assert row["Shares"] == 0
    assert np.isnan(row["Entry"])


def test_plan_columns_survive_a_ticker_with_no_tech_data(make_screened):
    """Missing price data must not break the row — NaN means fail, never crash."""
    row = generate_signals(make_screened({"NODATA": 6}), tech_data={}).iloc[0]
    assert row["Final Action Signal"] == "Hold"
    assert np.isnan(row["Entry"]) and row["Shares"] == 0


def test_output_columns_and_ordering(make_screened, setup_frame):
    screened = make_screened({"WATCH": 3, "BUY": 6, "HOLD": 5})
    tech = {"BUY": setup_frame}
    out = generate_signals(screened, tech)
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

def test_default_bullish_cutoff_is_three_of_five():
    """Calibrated against the measured score distribution: >=3 fires on ~9.4% of
    bars. The old >=4 fired on 0.9% — an event, not a posture."""
    assert math.ceil(DEFAULT_BULL_FRAC * len(TECHNICAL_COMPONENTS)) == 3


def test_default_bearish_cutoff_is_zero_of_five():
    """Bearish only when nothing fires (~4.9% of bars). The old <=1 covered
    55.5% of all bars, so the label carried no information."""
    assert math.floor(DEFAULT_BEAR_FRAC * len(TECHNICAL_COMPONENTS)) == 0


def test_posture_boundaries_for_the_default_registry():
    assert _posture(5, 5) == "Bullish"
    assert _posture(4, 5) == "Bullish"
    assert _posture(3, 5) == "Bullish"      # bull cutoff, inclusive
    assert _posture(2, 5) == "Neutral"      # the modal band (85.8% of bars)
    assert _posture(1, 5) == "Neutral"
    assert _posture(0, 5) == "Bearish"      # bear cutoff: nothing fired


def test_posture_bands_rescale_with_the_component_count():
    """Both cutoffs derive from max_score, so a resized registry rescales them."""
    assert [_posture(s, 3) for s in range(4)] == ["Bearish", "Neutral", "Bullish", "Bullish"]
    assert [_posture(s, 9) for s in (0, 1, 4, 5, 6, 9)] == [
        "Bearish", "Neutral", "Neutral", "Bullish", "Bullish", "Bullish"]


def test_posture_of_an_empty_registry_is_bearish():
    """No components -> nothing can confirm; must not divide by zero."""
    assert _posture(0, 0) == "Bearish"
