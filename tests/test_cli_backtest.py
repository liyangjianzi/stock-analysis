# tests/test_cli_backtest.py
from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest

from stockanalysis import cli
from stockanalysis.backtest import BacktestResults


def test_cli_backtest_invokes_run_backtest():
    fake = BacktestResults(mode="technical",
                           portfolio_summary={"total_return": 0.2, "cagr": 0.1,
                                              "max_drawdown": -0.1, "n_trades": 5,
                                              "win_rate": 0.6, "years": 2.0},
                           config={"entry_bucket": "Bullish"})
    with mock.patch("stockanalysis.backtest.run_backtest", return_value=fake) as m:
        rc = cli.main(["backtest", "--scope", "technical", "--no-report", "--no-excel"])
    assert rc == 0
    assert m.called
    assert m.call_args.kwargs["mode"] == "technical"


def test_cli_backtest_composite_warns(capsys):
    fake = BacktestResults(mode="composite", config={"entry_bucket": "Buy"})
    with mock.patch("stockanalysis.backtest.run_backtest", return_value=fake):
        cli.main(["backtest", "--scope", "composite", "--no-report", "--no-excel"])
    out = capsys.readouterr().out.upper()
    assert "LOOKAHEAD" in out or "CAVEAT" in out


def _plan_results():
    g = {"n": 100, "months": 20, "exp_r": 0.03, "se": 0.035, "ci_lo": -0.04,
         "ci_hi": 0.10, "t": 0.86, "p": 0.39}
    nl = dict(g, exp_r=0.07, ci_lo=0.01, ci_hi=0.13)
    edge = {"exp_r": -0.04, "se": 0.046, "ci_lo": -0.13, "ci_hi": 0.05, "p": 0.38,
            "verdict": "not distinguishable from random entry"}
    sec = {"gate": g, "null": nl, "edge": edge}
    return BacktestResults(
        mode="technical", config={"entry_bucket": "Bullish", "exits": "plan",
                                  "null_reps": 1},
        trades=[object()] * 100,
        trade_stats={"n": 100, "win_rate": 0.48, "avg_win_r": 1.1, "avg_loss_r": -0.9,
                     "expectancy_r": 0.03, "total_r": 3.0, "avg_bars_held": 20,
                     "exit_mix": {"stop": 52, "target": 48}, "se": 0.035,
                     "ci_lo": -0.04, "ci_hi": 0.10, "p": 0.39, "months": 20},
        robustness={"split_at": pd.Timestamp("2022-01-01"), "all": sec, "first": sec,
                    "second": sec, "yearly": {}, "years": 11, "positive_years": 6,
                    "negative_years": [2016, 2021, 2022, 2023, 2026]})


def test_cli_plan_backtest_prints_the_error_bar_and_the_null(capsys):
    with mock.patch("stockanalysis.backtest.run_backtest", return_value=_plan_results()):
        rc = cli.main(["backtest", "--exits", "plan", "--no-report", "--no-excel"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "95% CI [-0.04, +0.10]" in out and "p=0.39" in out
    assert "2022-01-01" in out
    assert "positive years: 6 of 11" in out and "2021" in out
    assert "edge vs random" in out
    assert "not distinguishable from random entry" in out


def test_cli_plan_backtest_says_why_there_is_no_html_report(capsys):
    with mock.patch("stockanalysis.backtest.run_backtest", return_value=_plan_results()):
        cli.main(["backtest", "--exits", "plan", "--no-excel"])
    out = capsys.readouterr().out
    assert "no HTML report for --exits plan" in out
    assert "Total return" not in out


def test_cli_passes_null_reps_and_split_through():
    with mock.patch("stockanalysis.backtest.run_backtest",
                    return_value=_plan_results()) as m:
        cli.main(["backtest", "--exits", "plan", "--null-reps", "0",
                  "--split", "2022-01-01", "--no-report", "--no-excel"])
    assert m.call_args.kwargs["null_reps"] == 0
    assert m.call_args.kwargs["split_at"] == "2022-01-01"


def test_cli_rejects_a_malformed_split_before_running(capsys):
    with mock.patch("stockanalysis.backtest.run_backtest") as m, \
            pytest.raises(SystemExit) as exc:
        cli.main(["backtest", "--exits", "plan", "--split", "2022-13-45"])
    assert exc.value.code == 2
    assert not m.called
    assert "YYYY-MM-DD" in capsys.readouterr().err
