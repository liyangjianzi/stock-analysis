"""Tests for account.py — one account taking the plan backtest's trades."""
from __future__ import annotations

import pandas as pd
import pytest

from stockanalysis.account import drawdown_odds, losing_streak, simulate_account, summarize
from stockanalysis.backtest import PlannedTrade


def _t(ticker, fill, exit_, *, entry=100.0, stop=90.0, exit_price=None, r=None):
    """A trade filled on ``fill`` and closed on ``exit_``; give either the exit
    price or the R-multiple."""
    if exit_price is None:
        exit_price = entry + r * (entry - stop)
    return PlannedTrade(ticker=ticker, entry_date=pd.Timestamp(fill) - pd.offsets.BDay(1),
                        entry=entry, stop=stop, target=entry * 2,
                        exit_date=pd.Timestamp(exit_), exit_price=exit_price,
                        exit_reason="test", r_multiple=(exit_price - entry) / (entry - stop),
                        bars_held=1, fill_date=pd.Timestamp(fill))


def test_a_trade_risks_the_set_fraction_of_equity():
    acct = simulate_account([_t("A", "2024-01-02", "2024-01-05", exit_price=120.0)],
                            start_equity=100_000, risk_pct=0.01, max_weight=1.0)
    row = acct["taken"].iloc[0]
    assert row["shares"] == 100                      # 1,000 risk / 10 per share
    assert row["pnl"] == pytest.approx(2_000.0)
    assert acct["total_return"] == pytest.approx(0.02)


def test_size_is_capped_by_the_position_weight():
    acct = simulate_account([_t("A", "2024-01-02", "2024-01-05", stop=99.0, r=1.0)],
                            start_equity=100_000, risk_pct=0.01, max_weight=0.20)
    assert acct["taken"].iloc[0]["shares"] == 200    # 20% of 100k at 100, not 1,000 shares


def test_no_margin_skips_what_the_cash_cannot_cover():
    trades = [_t(f"T{i}", "2024-01-02", "2024-01-10", stop=99.0, r=1.0) for i in range(6)]
    acct = simulate_account(trades, max_weight=0.20, gross_cap=1.0)
    assert acct["n_taken"] == 5                      # five 20% positions fill the account
    assert acct["skipped"]["cash"] == 1
    assert acct["max_gross"] == pytest.approx(1.0)


def test_a_heat_cap_limits_open_risk():
    trades = [_t(f"T{i}", "2024-01-02", "2024-01-10", r=1.0) for i in range(4)]
    acct = simulate_account(trades, risk_pct=0.01, heat_cap=0.02, max_weight=1.0, gross_cap=10.0)
    assert acct["n_taken"] == 2 and acct["skipped"]["heat"] == 2
    assert acct["max_heat"] == pytest.approx(0.02)


def test_an_exit_frees_its_cash_for_an_entry_the_same_day():
    trades = [_t(f"A{i}", "2024-01-02", "2024-01-05", stop=99.0, r=1.0) for i in range(5)]
    trades.append(_t("B", "2024-01-05", "2024-01-09", stop=99.0, r=1.0))
    assert simulate_account(trades, max_weight=0.20)["n_taken"] == 6


def test_same_day_entries_are_taken_in_a_seeded_random_order_not_alphabetically():
    trades = [_t(t, "2024-01-02", "2024-01-10", stop=99.0, r=1.0) for t in "ABCDEFGH"]
    pick = lambda seed: set(simulate_account(trades, max_weight=0.20, seed=seed)["taken"]["ticker"])
    assert pick(1) == pick(1)                                  # reproducible
    assert any(pick(s) != set("ABCDE") for s in range(5))      # not first-five-by-name


def test_sizing_compounds_on_realized_equity():
    trades = [_t("A", "2024-01-02", "2024-01-03", r=2.0), _t("B", "2024-01-04", "2024-01-05", r=1.0)]
    acct = simulate_account(trades, start_equity=100_000, risk_pct=0.01, max_weight=1.0)
    # after +2R the account is 102k, so B risks 1,020 -> 102 shares
    assert acct["taken"].iloc[1]["shares"] == 102


def test_drawdown_and_losing_streak():
    rs = [1, -1, -1, -1, 2]
    trades = [_t(f"T{i}", f"2024-01-{2 * i + 2:02d}", f"2024-01-{2 * i + 3:02d}", r=r)
              for i, r in enumerate(rs)]
    acct = simulate_account(trades, risk_pct=0.01, max_weight=1.0)
    assert acct["longest_losing_streak"] == 3
    assert acct["max_drawdown"] == pytest.approx(0.99 ** 3 - 1, rel=0.01)


def test_losing_streak_counts_consecutive_losses():
    assert losing_streak([1, -1, 0, -2, -1, 3, -1]) == 4        # a scratch (0) counts as a loss
    assert losing_streak([]) == 0


def test_a_drawdown_throttle_cuts_risk_until_a_new_high():
    trades = [_t(f"L{i}", f"2024-01-{i + 2:02d}", f"2024-01-{i + 2:02d}", r=-1.0) for i in range(11)]
    trades.append(_t("N", "2024-02-01", "2024-02-02", r=1.0))
    run = lambda **kw: simulate_account(trades, risk_pct=0.01, max_weight=1.0, **kw)["taken"]
    throttled = run(throttle_dd=0.10, throttle_factor=0.5)
    full = run()
    # 11 straight -1R put the account ~10.5% down, so the next trade is half size
    # (whole shares, so only approximately); the ten before it weren't throttled.
    assert throttled["risk"].iloc[-1] / full["risk"].iloc[-1] == pytest.approx(0.5, rel=0.03)
    assert (throttled["risk"].iloc[:10] == full["risk"].iloc[:10]).all()


def test_drawdown_odds_bracket_the_obvious_cases():
    months = pd.date_range("2020-01-31", periods=24, freq="ME")
    winners = [(m, 1.0) for m in months for _ in range(5)]
    losers = [(m, -1.0) for m in months for _ in range(5)]
    win = drawdown_odds(winners, risk_pcts=(0.01,), reps=200).iloc[0]
    lose = drawdown_odds(losers, risk_pcts=(0.01,), reps=200).iloc[0]
    assert win["p_dd_20"] == 0.0
    assert lose["p_dd_20"] == 1.0                                # 120 straight -1R at 1%


def test_drawdown_odds_grow_with_risk_per_trade():
    months = pd.date_range("2020-01-31", periods=60, freq="ME")
    rs = [(m, r) for i, m in enumerate(months) for r in ([1.2, -1.0, -1.0, 1.1] if i % 2 else [-1.0, 1.3])]
    odds = drawdown_odds(rs, risk_pcts=(0.005, 0.02), reps=300).set_index("risk_pct")
    assert odds.loc[0.02, "dd_p95"] > odds.loc[0.005, "dd_p95"]


def test_summarize_runs_the_gate_and_the_null_through_the_same_rules():
    gate = [_t(f"G{i}", f"2024-0{i % 9 + 1}-02", f"2024-0{i % 9 + 1}-09", r=1.0) for i in range(9)]
    null = [_t(f"N{i}", f"2024-0{i % 9 + 1}-03", f"2024-0{i % 9 + 1}-10", r=-1.0) for i in range(9)]
    out = summarize(gate, null, {"risk_pct": 0.01, "max_weight": 1.0})
    assert out["gate"]["total_return"] > 0 > out["null"]["total_return"]
    # the rules that ran, resolved against simulate_account's defaults
    assert out["params"]["risk_pct"] == 0.01 and out["params"]["max_weight"] == 1.0
    assert out["params"]["gross_cap"] == 1.0 and out["params"]["heat_cap"] is None
    assert list(out["odds"]["risk_pct"]) == [0.005, 0.01, 0.02]


def test_summarize_without_a_null_leaves_it_empty():
    out = summarize([_t("G", "2024-01-02", "2024-01-09", r=1.0)], None, {})
    assert out["null"] is None


def test_drawdown_odds_use_what_each_trade_really_risked():
    """A tight stop hits the 20% position cap, so the trade risks far less than
    1%: 30 losers at -1R cost ~0.2% each, not 1%. The odds must bootstrap the
    realized loss, not 1% x R."""
    months = pd.date_range("2020-01-31", periods=30, freq="ME")
    losers = [_t(f"T{i}", m - pd.offsets.BDay(3), m, stop=99.0, r=-1.0) for i, m in enumerate(months)]
    out = summarize(losers, None, {"risk_pct": 0.01, "max_weight": 0.20})
    one_pct = out["odds"].set_index("risk_pct").loc[0.01]
    assert out["gate"]["max_drawdown"] > -0.07                 # ~30 x 0.2%
    assert one_pct["p_dd_20"] == 0.0                           # 1% x R would say 26% -> 1.0


def test_the_result_does_not_depend_on_the_order_trades_arrive_in():
    """Only the seed may decide which same-day setups get in — the CLI and a
    research script load tickers in different orders."""
    trades = [_t(t, "2024-01-02", "2024-01-10", stop=99.0, r=(1.0 if t in "ACE" else -1.0))
              for t in "ABCDEFGH"]
    a = simulate_account(trades, max_weight=0.20, seed=3)
    b = simulate_account(list(reversed(trades)), max_weight=0.20, seed=3)
    assert set(a["taken"]["ticker"]) == set(b["taken"]["ticker"])
    assert a["total_return"] == b["total_return"]


def test_summarize_reports_the_spread_across_orderings():
    trades = [_t(f"T{i}", f"2024-{m:02d}-02", f"2024-{m:02d}-20", stop=99.0,
                 r=(2.0 if i % 3 == 0 else -1.0)) for m in range(1, 13) for i in range(8)]
    out = summarize(trades, None, {"max_weight": 0.20}, seeds=10)
    spread = out["spread"]
    assert spread["seeds"] == 10
    lo, mid, hi = spread["cagr"]
    assert lo <= mid <= hi and lo < hi                  # different picks, different outcomes
    assert set(spread) >= {"max_drawdown", "longest_losing_streak"}
