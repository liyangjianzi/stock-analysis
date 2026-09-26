"""Test backtest output builders (charts and reports).

Fully offline: fixtures are synthetic OHLCV data.
"""
from __future__ import annotations

import openpyxl
import pandas as pd

from stockanalysis import charts
from stockanalysis.backtest import build_results_from_prices, run_backtest
from stockanalysis.outputs.backtest_excel import write_backtest_workbook


def test_build_backtest_report_returns_figure(uptrend_ohlcv, downtrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv, "DOWN": downtrend_ohlcv},
                                    horizons=("1m", "3m"), max_hold="1m")
    fig = charts.build_backtest_report(res)
    assert fig is not None
    assert len(fig.data) >= 1            # at least the equity curve


def test_build_backtest_report_flags_composite(uptrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv}, mode="composite",
                                    fundamental_scores={"UP": 6}, horizons=("1m",),
                                    max_hold="1m")
    fig = charts.build_backtest_report(res)
    assert "COMPOSITE" in fig.layout.title.text.upper()


def test_write_backtest_workbook(tmp_path, uptrend_ohlcv, downtrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv, "DOWN": downtrend_ohlcv},
                                    horizons=("1m", "3m"), max_hold="1m")
    path = tmp_path / "bt.xlsx"
    out = write_backtest_workbook(res, path)

    wb = openpyxl.load_workbook(out)
    assert "Backtest Summary" in wb.sheetnames
    assert "Event Study" in wb.sheetnames


def test_plan_workbook_carries_the_robustness_and_yearly_sheets(tmp_path, uptrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv}, exits="plan", max_hold="1m")
    wb = openpyxl.load_workbook(write_backtest_workbook(res, tmp_path / "bt.xlsx"))
    assert "Robustness" in wb.sheetnames and "Yearly R" in wb.sheetnames

    ws = wb["Robustness"]
    header = [c.value for c in ws[1]]
    assert header[:3] == ["Series", "Period", "n"]
    assert {"exp_r", "ci_lo", "ci_hi", "p", "verdict"} <= set(header)
    rows = {(r[0], r[1]) for r in ws.iter_rows(min_row=2, values_only=True)}
    assert {("Gate", "All"), ("Null", "Second half"), ("Edge", "First half")} <= rows


def test_plan_workbook_summary_is_the_gate_and_its_plan(tmp_path, uptrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv}, exits="plan", max_hold="1m")
    wb = openpyxl.load_workbook(write_backtest_workbook(res, tmp_path / "bt.xlsx"))
    assert "Event Study" not in wb.sheetnames

    ws = wb["Backtest Summary"]
    row = dict(zip([c.value for c in ws[1]], [c.value for c in ws[2]]))
    assert row["entries"] == "gate" and row["exits"] == "plan"
    assert "expectancy_r" in row
    assert not {"total_return", "max_drawdown", "cagr"} & set(row)


def test_plan_run_writes_no_html_report(tmp_path, uptrend_ohlcv):
    run_backtest(prices={"UP": uptrend_ohlcv}, exits="plan", max_hold="1m",
                 null_reps=0, benchmark=None, out_dir=tmp_path / "plan")
    assert not list((tmp_path / "plan").rglob("backtest_report.html"))

    run_backtest(prices={"UP": uptrend_ohlcv}, max_hold="1m",
                 benchmark=None, out_dir=tmp_path / "horizon")
    assert list((tmp_path / "horizon").rglob("backtest_report.html"))


def test_horizon_workbook_has_no_robustness_sheet(tmp_path, uptrend_ohlcv):
    res = build_results_from_prices({"UP": uptrend_ohlcv}, horizons=("1m",), max_hold="1m")
    wb = openpyxl.load_workbook(write_backtest_workbook(res, tmp_path / "bt.xlsx"))
    assert "Robustness" not in wb.sheetnames
