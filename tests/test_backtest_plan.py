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
                                    build_results_from_prices, entry_events,
                                    posture_timeline, random_entry_trades,
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

def _t(r, reason="stop", *, ticker="T", entry_date=None):
    return PlannedTrade(ticker=ticker, entry_date=entry_date, entry=10.0, stop=9.0,
                        target=12.0, exit_date=None, exit_price=0.0,
                        exit_reason=reason, r_multiple=r, bars_held=1)


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
    assert np.isnan(s["ci_lo"]) and s["months"] == 0


def test_aggregate_trade_stats_carries_a_month_clustered_error_bar():
    """The bare expectancy is never reported alone: the CI is clustered by entry
    month (see robustness.cluster_expectancy), so two months are two draws."""
    dated = [_t(r, entry_date=pd.Timestamp(d))
             for d, r in [("2024-01-05", 1.0), ("2024-01-09", 1.0),
                          ("2024-02-05", -1.0), ("2024-02-09", -1.0)]]
    s = aggregate_trade_stats(dated)
    assert s["months"] == 2
    assert s["se"] == pytest.approx(1.0)
    assert s["ci_lo"] == pytest.approx(-1.96) and s["ci_hi"] == pytest.approx(1.96)
    assert s["p"] == pytest.approx(1.0)


# --- the random-entry null --------------------------------------------------------

def _gate_trades(ticker, k):
    return [_t(0.0, ticker=ticker)] * k


def test_random_entries_are_ticker_matched_and_inside_the_eligible_range():
    h = _prelude(200)
    null = random_entry_trades({"T": h, "U": h}, _gate_trades("T", 3), reps=2,
                               seed=1, cost_bps=0.0)
    assert len(null) == 6                            # 3 gate trades x 2 reps
    assert {t.ticker for t in null} == {"T"}         # never "U": it had no trades
    pos = {ts: i for i, ts in enumerate(h.index)}
    assert all(60 <= pos[t.entry_date] <= len(h) - 2 for t in null)


def test_random_entries_are_deterministic_for_a_seed():
    h = _prelude(200)
    draw = lambda seed: [t.entry_date for t in random_entry_trades(
        {"T": h}, _gate_trades("T", 5), seed=seed, cost_bps=0.0)]
    assert draw(7) == draw(7)
    assert draw(7) != draw(8)


def test_random_entries_skip_tickers_too_short_to_trade():
    assert random_entry_trades({"T": _prelude(61)}, _gate_trades("T", 2)) == []
    assert random_entry_trades({}, _gate_trades("T", 2)) == []


def test_plan_exits_reuse_the_single_replay_and_populate_stats(prelude):
    """Integration: build_results_from_prices(exits="plan") wires entries ->
    walker -> stats without a second timeline pass."""
    p = _plan_for(prelude)
    entry = p["entry"]
    hist = _append(prelude, [(entry, p["target"] + 1, entry - 0.1, p["target"])] * 3)

    r = build_results_from_prices({"T": hist}, exits="plan", cost_bps=0.0)
    assert r.config["exits"] == "plan"
    assert r.trade_stats["n"] == len(r.trades)
    # every replay records the gate, which is what the plan exits consume
    assert "gate" in posture_timeline(hist).columns


def test_horizon_exits_stay_the_default_and_record_no_trades(prelude):
    r = build_results_from_prices({"T": prelude})
    assert r.config["exits"] == "horizon"
    assert r.trades == [] and r.trade_stats == {}
    assert r.robustness == {}


def test_plan_exits_report_halves_and_the_random_null(uptrend_ohlcv):
    r = build_results_from_prices({"UP": uptrend_ohlcv}, exits="plan",
                                  max_hold="1m", null_seed=3)
    rb = r.robustness
    assert set(rb) >= {"split_at", "all", "first", "second", "yearly"}
    assert rb["all"]["gate"]["n"] == len(r.trades)
    assert rb["all"]["null"] is not None             # null on by default
    # default split: the calendar midpoint of the price history
    idx = uptrend_ohlcv.index
    assert rb["split_at"] == (idx[0] + (idx[-1] - idx[0]) / 2).floor("D")
    assert r.config["null_reps"] == 1 and r.config["null_seed"] == 3
    assert r.config["split_at"] == rb["split_at"]


def test_plan_exits_skip_the_posture_label_sim_and_event_study(uptrend_ohlcv):
    # Both describe a different rule (enter on Bullish, exit when it fades); plan
    # mode reports the gate's own trades only. Horizon mode keeps them.
    plan = build_results_from_prices({"UP": uptrend_ohlcv}, exits="plan", max_hold="1m")
    assert plan.portfolio_curve.empty
    assert plan.portfolio_summary == {}
    assert plan.event_stats == {}

    horizon = build_results_from_prices({"UP": uptrend_ohlcv}, max_hold="1m")
    assert not horizon.portfolio_curve.empty
    assert "max_drawdown" in horizon.portfolio_summary
    assert "Bullish" in horizon.event_stats


def test_plan_exits_honour_an_explicit_split_and_a_skipped_null(uptrend_ohlcv):
    r = build_results_from_prices({"UP": uptrend_ohlcv}, exits="plan",
                                  max_hold="1m", null_reps=0, split_at="2023-06-01")
    assert r.robustness["split_at"] == pd.Timestamp("2023-06-01")
    assert r.robustness["all"]["null"] is None
    assert r.robustness["all"]["edge"] == {}


# --- the fast replay path ------------------------------------------------------

def test_fast_replay_is_identical_to_the_slice_by_slice_replay():
    """The whole justification for `fast=True`: every column the predicates read
    is causal, so one pass must reproduce the per-slice result exactly. If a
    non-causal component is ever added to the registry, this test fails first."""
    from conftest import pullback_ohlcv
    hist = pullback_ohlcv(n=300)

    slow = posture_timeline(hist, min_bars=60)
    fast = posture_timeline(hist, min_bars=60, fast=True)

    pd.testing.assert_frame_equal(slow, fast)


def test_fast_replay_matches_in_gate_mode_too(uptrend_ohlcv):
    slow = posture_timeline(uptrend_ohlcv, mode="gate", min_bars=60)
    fast = posture_timeline(uptrend_ohlcv, mode="gate", min_bars=60, fast=True)
    pd.testing.assert_frame_equal(slow, fast)


def test_fast_replay_is_much_cheaper(prelude):
    import time
    hist = _append(prelude, [(100, 101, 99, 100)] * 200)
    t0 = time.perf_counter(); posture_timeline(hist, min_bars=60); slow = time.perf_counter() - t0
    t1 = time.perf_counter(); posture_timeline(hist, min_bars=60, fast=True); quick = time.perf_counter() - t1
    assert quick < slow / 2, f"fast={quick:.3f}s vs slow={slow:.3f}s"


def test_trade_plans_from_one_indicator_pass_match_per_slice_plans():
    """simulate_planned_trades computes indicators once per ticker and slices,
    which is exact only because every column build_trade_plan reads (Close,
    ATR14, the High/Low pivots behind the S/R levels) is causal. A wandering
    series leaves real levels, so the S/R stop/target path is exercised too."""
    from conftest import wandering_ohlcv
    hist = wandering_ohlcv(100.0, n=300)
    once = add_indicators(hist)
    plans = [(build_trade_plan(add_indicators(hist.iloc[: i + 1])),
              build_trade_plan(once.iloc[: i + 1])) for i in range(60, 300, 7)]
    assert any(p["stop_basis"] == "structure" for p, _ in plans), "no S/R-based plan"
    for per_slice, sliced in plans:
        assert per_slice == pytest.approx(sliced, nan_ok=True)
