# tests/test_cli_backtest.py
from __future__ import annotations

from unittest import mock

import pandas as pd

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
