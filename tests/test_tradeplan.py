"""Trade plan: stop placement, target selection and position sizing.

Fully offline — synthetic OHLCV only. ``wandering_ohlcv`` is the helper that
actually leaves swing pivots (a clean monotonic trend leaves none), so it drives
the structure-based cases while ``uptrend_ohlcv`` drives the ATR fallbacks.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from conftest import wandering_ohlcv as _wandering_ohlcv

from stockanalysis.config import (ATR_STOP_MULT, DEFAULT_ACCOUNT_SIZE,
                                  DEFAULT_MAX_WEIGHT, DEFAULT_RISK_PCT,
                                  MIN_STOP_ATR, MIN_TARGET_ATR, STOP_BUFFER_ATR)
from stockanalysis.indicators import add_indicators, find_support_resistance
from stockanalysis.tradeplan import (MATRIX_COLUMNS, _SR_MAX_LEVELS,
                                     build_trade_plan, empty_plan)

# The shipped defaults, not copies of them — a config change must reach the tests.
ACCOUNT = DEFAULT_ACCOUNT_SIZE
RISK_PCT = DEFAULT_RISK_PCT
MAX_WEIGHT = DEFAULT_MAX_WEIGHT


def _plan(df, **kw):
    kw.setdefault("account_size", ACCOUNT)
    kw.setdefault("risk_pct", RISK_PCT)
    kw.setdefault("max_weight", MAX_WEIGHT)
    return build_trade_plan(df, **kw)


@pytest.fixture
def wandering():
    """Indicator-enriched random walk — carries ATR14 and S/R levels."""
    return add_indicators(_wandering_ohlcv(150.0))


# --- Stop placement ----------------------------------------------------------

def test_stop_uses_the_nearest_usable_support_not_the_strongest(wandering):
    plan = _plan(wandering)
    entry = wandering["Close"].iloc[-1]
    atr = wandering["ATR14"].iloc[-1]

    levels = find_support_resistance(wandering, max_levels=_SR_MAX_LEVELS)
    usable = [L["level"] for L in levels if L["level"] <= entry - MIN_STOP_ATR * atr]
    assert usable, "fixture should leave usable support below price"

    assert plan["stop_basis"] == "structure"
    # The nearest level clearing the noise floor — note find_support_resistance
    # ranks by touch count, so this is not simply levels[0].
    assert plan["stop"] == pytest.approx(max(usable) - STOP_BUFFER_ATR * atr)
    assert plan["stop"] < entry


def test_stop_skips_support_sitting_inside_the_noise_floor(wandering):
    """Price often sits *on* a level; a stop a fraction of a percent away is
    taken out by an ordinary day's range, so such levels are skipped."""
    plan = _plan(wandering)
    atr = wandering["ATR14"].iloc[-1]
    assert plan["entry"] - plan["stop"] >= MIN_STOP_ATR * atr


def test_stop_falls_back_to_atr_when_no_support_below(uptrend_ohlcv):
    df = add_indicators(uptrend_ohlcv)
    assert find_support_resistance(df, max_levels=_SR_MAX_LEVELS) == []

    plan = _plan(df)
    entry, atr = df["Close"].iloc[-1], df["ATR14"].iloc[-1]
    assert plan["stop_basis"] == "atr"
    assert plan["stop"] == pytest.approx(entry - ATR_STOP_MULT * atr)


def test_atr_stop_mult_is_configurable(uptrend_ohlcv):
    df = add_indicators(uptrend_ohlcv)
    entry, atr = df["Close"].iloc[-1], df["ATR14"].iloc[-1]
    assert _plan(df, atr_stop_mult=3.0)["stop"] == pytest.approx(entry - 3.0 * atr)


# --- Target selection --------------------------------------------------------

def test_target_uses_the_nearest_usable_resistance(wandering):
    plan = _plan(wandering)
    entry, atr = wandering["Close"].iloc[-1], wandering["ATR14"].iloc[-1]
    usable = [L["level"] for L in find_support_resistance(wandering, max_levels=_SR_MAX_LEVELS)
              if L["level"] >= entry + MIN_TARGET_ATR * atr]
    if not usable:
        pytest.skip("fixture left no usable resistance above price")
    assert plan["target_basis"] == "structure"
    assert plan["target"] == pytest.approx(min(usable))


def test_target_skips_resistance_inside_the_floor(wandering):
    """Overhead supply 0.2% away is not a target — quoting it would produce a
    meaningless R:R like 0.05."""
    plan = _plan(wandering)
    entry, atr = wandering["Close"].iloc[-1], wandering["ATR14"].iloc[-1]
    assert plan["target"] - entry >= MIN_TARGET_ATR * atr


def test_target_falls_back_to_2r(uptrend_ohlcv):
    df = add_indicators(uptrend_ohlcv)          # no levels at all
    plan = _plan(df)
    assert plan["target_basis"] == "2R"
    assert plan["target"] == pytest.approx(plan["entry"] + 2 * (plan["entry"] - plan["stop"]))
    assert plan["rr"] == pytest.approx(2.0)


# --- Sizing ------------------------------------------------------------------

def test_shares_risk_the_configured_fraction(uptrend_ohlcv):
    df = add_indicators(uptrend_ohlcv)
    plan = _plan(df, max_weight=1.0)         # lift the cap to isolate the risk math
    risk_per_share = plan["entry"] - plan["stop"]
    assert plan["shares"] == math.floor(ACCOUNT * RISK_PCT / risk_per_share)
    assert plan["risk_amount"] == pytest.approx(plan["shares"] * risk_per_share)
    assert plan["risk_amount"] <= ACCOUNT * RISK_PCT


def test_max_weight_cap_binds_on_a_tight_stop(uptrend_ohlcv):
    df = add_indicators(uptrend_ohlcv)
    # A big risk budget would buy far more than the weight cap allows.
    plan = _plan(df, risk_pct=0.50)
    cap = math.floor(ACCOUNT * MAX_WEIGHT / plan["entry"])
    assert plan["shares"] == cap
    assert plan["shares"] * plan["entry"] <= ACCOUNT * MAX_WEIGHT


def test_shares_are_zero_when_one_share_exceeds_the_budget(uptrend_ohlcv):
    df = add_indicators(uptrend_ohlcv)
    plan = _plan(df, account_size=100.0)
    assert plan["shares"] == 0
    assert plan["risk_amount"] == 0.0
    # The levels are still reported — the trader may size differently.
    assert np.isfinite(plan["stop"]) and np.isfinite(plan["target"])


# --- Degenerate input: NaN means fail, never crash ---------------------------

@pytest.mark.parametrize("df", [
    None,
    pd.DataFrame(),
    pd.DataFrame({"Close": [10.0, 11.0]}),                      # no ATR14 column
], ids=["none", "empty", "no-atr"])
def test_degenerate_input_returns_an_empty_plan(df):
    plan = _plan(df)
    # Not `plan == empty_plan()`: NaN != NaN, so that would only pass by np.nan
    # identity. Compare the shape and the non-NaN fields explicitly.
    assert set(plan) == set(empty_plan())
    assert (plan["shares"], plan["risk_amount"]) == (0, 0.0)
    assert plan["stop_basis"] is None and plan["target_basis"] is None
    assert all(np.isnan(plan[k]) for k in ("entry", "stop", "target", "rr"))


def test_zero_atr_returns_an_empty_plan():
    """A flat series gives ATR14 == 0 — dividing by it would explode."""
    idx = pd.bdate_range("2024-01-01", periods=60)
    flat = pd.DataFrame({"Open": 50.0, "High": 50.0, "Low": 50.0, "Close": 50.0,
                         "Volume": 1_000_000}, index=idx)
    plan = _plan(add_indicators(flat))
    assert plan["shares"] == 0
    assert np.isnan(plan["stop"])


def test_a_populated_plan_has_the_same_shape_as_an_empty_one(wandering):
    """The two producers are the whole contract — they must not drift apart."""
    assert set(_plan(wandering)) == set(empty_plan()) == set(MATRIX_COLUMNS)
