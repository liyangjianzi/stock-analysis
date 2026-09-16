"""Plan-based exits: walk each entry to its stop / target / time stop.

Fully offline. Every frame here is a *monotonic* prelude — which leaves no swing
pivots, so ``find_support_resistance`` returns [] and the plan falls back to its
deterministic ATR stop and 2R target. That makes entry/stop/target exactly
predictable, so the R-multiples below are asserted to the cent rather than
approximated.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalysis.backtest import (PlannedTrade, aggregate_trade_stats,
                                    entry_events, posture_timeline,
                                    simulate_planned_trades)
from stockanalysis.indicators import add_indicators, find_support_resistance
from stockanalysis.tradeplan import build_trade_plan

PRELUDE = 80


def _prelude(n: int = PRELUDE, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    """A clean up-drift: nonzero ATR, and deliberately no swing pivots."""
    idx = pd.bdate_range("2024-01-02", periods=n)
    close = pd.Series(start + np.arange(n) * step, index=idx)
    openp = close.shift(1).fillna(close.iloc[0])
    return pd.DataFrame({
        "Open": openp,
        "High": np.maximum(openp, close) * 1.005,
        "Low": np.minimum(openp, close) * 0.995,
        "Close": close,
        "Volume": pd.Series(1_000_000, index=idx),
    })


def _plan_for(hist: pd.DataFrame) -> dict:
    return build_trade_plan(add_indicators(hist))


def _append(hist: pd.DataFrame, bars: list[tuple]) -> pd.DataFrame:
    """Append explicit (open, high, low, close) bars on the next business days."""
    idx = pd.bdate_range(hist.index[-1] + pd.offsets.BDay(1), periods=len(bars))
    tail = pd.DataFrame(
        [{"Open": o, "High": h, "Low": lo, "Close": c, "Volume": 1_000_000}
         for o, h, lo, c in bars], index=idx)
    return pd.concat([hist, tail])


def _one(hist, entry_date, **kw):
    kw.setdefault("cost_bps", 0.0)
    trades = simulate_planned_trades(hist, [entry_date], ticker="T", **kw)
    assert len(trades) == 1
    return trades[0]


@pytest.fixture
def prelude():
    h = _prelude()
    assert find_support_resistance(h) == [], "fixture must leave no S/R levels"
    return h


# --- the fixture's own contract ------------------------------------------------

def test_plan_on_the_prelude_is_the_deterministic_atr_fallback(prelude):
    plan = _plan_for(prelude)
    assert (plan["stop_basis"], plan["target_basis"]) == ("atr", "2R")
    risk = plan["entry"] - plan["stop"]
    assert plan["target"] == pytest.approx(plan["entry"] + 2 * risk)


# --- exit paths ----------------------------------------------------------------

def test_target_hit_pays_two_r(prelude):
    p = _plan_for(prelude)
    entry = p["entry"]                                   # next bar opens here
    hist = _append(prelude, [(entry, p["target"] + 1, entry - 0.1, p["target"])])
    t = _one(hist, prelude.index[-1])

    assert t.exit_reason == "target"
    assert t.exit_price == pytest.approx(p["target"])
    assert t.r_multiple == pytest.approx(2.0)


def test_stop_hit_loses_exactly_one_r(prelude):
    p = _plan_for(prelude)
    entry = p["entry"]
    hist = _append(prelude, [(entry, entry + 0.1, p["stop"] - 1, p["stop"])])
    t = _one(hist, prelude.index[-1])

    assert t.exit_reason == "stop"
    assert t.exit_price == pytest.approx(p["stop"])
    assert t.r_multiple == pytest.approx(-1.0)


def test_a_gap_through_the_stop_loses_more_than_one_r(prelude):
    """The realistic bad case: the open is already below the stop, so the fill is
    worse than the stop and R comes in below -1."""
    p = _plan_for(prelude)
    entry, gap = p["entry"], p["stop"] - 5.0
    hist = _append(prelude, [(entry, entry, entry, entry),      # day 1: nothing
                             (gap, gap + 0.5, gap - 0.5, gap)])  # day 2: gaps down
    t = _one(hist, prelude.index[-1])

    assert t.exit_reason == "stop_gap"
    assert t.exit_price == pytest.approx(gap)
    assert t.r_multiple < -1.0


def test_stop_wins_when_one_bar_touches_both(prelude):
    """Daily OHLC can't order intrabar events, so we assume the worst path."""
    p = _plan_for(prelude)
    entry = p["entry"]
    hist = _append(prelude, [(entry, p["target"] + 1, p["stop"] - 1, entry)])
    t = _one(hist, prelude.index[-1])

    assert t.exit_reason == "stop"
    assert t.r_multiple == pytest.approx(-1.0)


def test_neither_level_hit_exits_on_the_time_stop(prelude):
    p = _plan_for(prelude)
    entry = p["entry"]
    flat = [(entry, entry + 0.2, entry - 0.2, entry + 0.1)] * 5
    hist = _append(prelude, flat)
    t = _one(hist, prelude.index[-1], max_hold_bars=3)

    assert t.exit_reason == "time"
    assert t.bars_held == 3
    assert t.exit_price == pytest.approx(hist["Close"].iloc[PRELUDE + 3 - 1 + 1])


def test_target_gap_fills_better_than_the_target(prelude):
    p = _plan_for(prelude)
    entry, gap = p["entry"], p["target"] + 4.0
    hist = _append(prelude, [(entry, entry, entry, entry),
                             (gap, gap + 1, gap - 1, gap)])
    t = _one(hist, prelude.index[-1])

    assert t.exit_reason == "target_gap"
    assert t.r_multiple > 2.0


# --- guards --------------------------------------------------------------------

def test_a_ticker_with_no_usable_plan_produces_no_trade():
    """Flat series -> ATR 0 -> empty_plan -> nothing to simulate (never raises)."""
    idx = pd.bdate_range("2024-01-02", periods=80)
    flat = pd.DataFrame({"Open": 50.0, "High": 50.0, "Low": 50.0, "Close": 50.0,
                         "Volume": 1_000_000}, index=idx)
    assert simulate_planned_trades(flat, [idx[-5]], ticker="FLAT") == []


def test_an_entry_on_the_last_bar_is_skipped(prelude):
    """There is no next bar to fill on."""
    assert simulate_planned_trades(prelude, [prelude.index[-1]], ticker="T") == []


def test_costs_reduce_the_realized_r(prelude):
    p = _plan_for(prelude)
    entry = p["entry"]
    hist = _append(prelude, [(entry, p["target"] + 1, entry - 0.1, p["target"])])

    free = _one(hist, prelude.index[-1], cost_bps=0.0)
    charged = _one(hist, prelude.index[-1], cost_bps=50.0)
    assert charged.r_multiple < free.r_multiple


# --- gate entries --------------------------------------------------------------

def test_gate_mode_labels_bars_by_the_entry_gate(uptrend_ohlcv):
    from conftest import pullback_ohlcv
    tl = posture_timeline(pullback_ohlcv(), mode="gate", min_bars=60)
    assert set(tl["label"]) <= {"Entry", "Flat"}
    assert tl["label"].iloc[-1] == "Entry"          # the fixture fires the gate
    # and the de-overlap helper works unchanged on these labels
    assert entry_events(tl, ("Entry",))


def test_gate_mode_finds_nothing_in_a_downtrend(downtrend_ohlcv):
    tl = posture_timeline(downtrend_ohlcv, mode="gate", min_bars=60)
    assert (tl["label"] == "Entry").sum() == 0


# --- aggregation ---------------------------------------------------------------

def _t(r, reason="stop"):
    return PlannedTrade(ticker="T", entry_date=None, entry=10.0, stop=9.0, target=12.0,
                        exit_date=None, exit_price=0.0, exit_reason=reason,
                        r_multiple=r, bars_held=1)


def test_aggregate_trade_stats_computes_expectancy_in_r():
    trades = [_t(2.0, "target"), _t(2.0, "target"), _t(-1.0), _t(-1.0), _t(-1.0)]
    s = aggregate_trade_stats(trades)

    assert s["n"] == 5
    assert s["win_rate"] == pytest.approx(0.4)
    assert s["avg_win_r"] == pytest.approx(2.0)
    assert s["avg_loss_r"] == pytest.approx(-1.0)
    # 0.4*2 + 0.6*(-1) = +0.20R per trade
    assert s["expectancy_r"] == pytest.approx(0.2)
    assert s["total_r"] == pytest.approx(1.0)
    assert s["exit_mix"] == {"target": 2, "stop": 3}


def test_aggregate_trade_stats_on_no_trades_is_empty_not_a_crash():
    s = aggregate_trade_stats([])
    assert s["n"] == 0
    assert np.isnan(s["expectancy_r"])
    assert s["exit_mix"] == {}


def test_plan_exits_reuse_the_single_replay_and_populate_stats(prelude):
    """Integration: build_results_from_prices(exits="plan") wires entries ->
    walker -> stats without a second timeline pass."""
    from stockanalysis.backtest import build_results_from_prices
    p = _plan_for(prelude)
    entry = p["entry"]
    hist = _append(prelude, [(entry, p["target"] + 1, entry - 0.1, p["target"])] * 3)

    r = build_results_from_prices({"T": hist}, exits="plan", cost_bps=0.0)
    assert r.config["exits"] == "plan"
    assert r.trade_stats["n"] == len(r.trades)
    # every replay records the gate, which is what the plan exits consume
    assert "gate" in posture_timeline(hist).columns


def test_horizon_exits_stay_the_default_and_record_no_trades(prelude):
    from stockanalysis.backtest import build_results_from_prices
    r = build_results_from_prices({"T": prelude})
    assert r.config["exits"] == "horizon"
    assert r.trades == [] and r.trade_stats == {}
