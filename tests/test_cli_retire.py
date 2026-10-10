"""`stock-analysis retire` through the real argparse dispatch; offline, invented numbers."""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stockanalysis import cli, holdings
from stockanalysis.retirement import inputs, optimize

FAST = ["--paths", "40", "--scenario-paths", "20", "--bank", "/nonexistent-bank"]  # never the owner's


def _plan(tmp_path, *, balances=True) -> Path:
    d = copy.deepcopy(inputs.TEMPLATE)
    if not balances:
        d.pop("balances")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(d))
    return path


def test_init_writes_and_refuses_to_overwrite(tmp_path, capsys):
    path = tmp_path / "plan.json"
    assert cli.main(["retire", "--init", "--inputs", str(path)]) == 0 and path.exists()
    assert cli.main(["retire", "--init", "--inputs", str(path)]) == 1
    assert "already exists" in capsys.readouterr().err


def test_missing_plan_suggests_init(tmp_path, capsys):
    assert cli.main(["retire", "--inputs", str(tmp_path / "none.json")]) == 1
    assert "--init" in capsys.readouterr().err


def test_pinned_balances_write_report_and_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(holdings, "load", lambda *a, **k: pytest.fail("holdings must not be read"))
    out = tmp_path / "out"
    rc = cli.main(["retire", "--inputs", str(_plan(tmp_path)), "--out", str(out), *FAST])
    assert rc == 0
    [run_dir] = list(out.iterdir())
    assert (run_dir / "retirement_report.html").exists() and (run_dir / "summary.json").exists()
    printed = capsys.readouterr().out
    assert "Chance the money lasts as long as either of you lives" in printed and "Report:" in printed


def test_reads_holdings_when_plan_has_no_balances(tmp_path, monkeypatch):
    frame = pd.DataFrame(
        [["Partner A RRSP", "cash", np.nan, np.nan, 400_000.0, 400_000.0],
         ["Partner B TFSA", "cash", np.nan, np.nan, 90_000.0, 90_000.0]],
        columns=["account", "kind", "shares", "cost", "value", "value_cad"])
    monkeypatch.setattr(holdings, "load", lambda path=None: {
        "holdings": frame, "path": Path("book.xlsx"), "saved_at": dt.datetime(2026, 9, 1, 8, 0)})
    out = tmp_path / "out"
    assert cli.main(["retire", "--inputs", str(_plan(tmp_path, balances=False)),
                     "--out", str(out), *FAST]) == 0
    [run_dir] = list(out.iterdir())
    assert "book.xlsx, saved 2026-09-01 08:00" in (run_dir / "retirement_report.html").read_text()


def test_no_balances_and_no_holdings_names_both(tmp_path, monkeypatch, capsys):
    def missing(path=None):
        raise FileNotFoundError("No holdings file at x")
    monkeypatch.setattr(holdings, "load", missing)
    rc = cli.main(["retire", "--inputs", str(_plan(tmp_path, balances=False)),
                   "--out", str(tmp_path / "out"), *FAST])
    assert rc == 1
    err = capsys.readouterr().err
    assert "balances" in err and "--holdings" in err


def test_suggestions_default_to_the_same_futures_as_the_gauge():
    assert cli.build_parser().parse_args(["retire"]).scenario_paths is None


def test_optimize_prints_the_answers_without_a_report(tmp_path, monkeypatch, capsys):
    small = optimize.best_benefit_ages
    monkeypatch.setattr(optimize, "best_benefit_ages",
                        lambda plan, **kw: small(plan, cpp_ages=(65, 70), oas_ages=(65, 70), workers=1, **kw))
    draw = optimize.rrsp_drawdown                      # a small grid, no process pool
    monkeypatch.setattr(optimize, "rrsp_drawdown",
                        lambda plan, **kw: draw(plan, targets=(0, 30_000), workers=1, **kw))
    out = tmp_path / "out"
    assert cli.main(["retire", "--inputs", str(_plan(tmp_path)), "--out", str(out),
                     "--optimize", "--target", "80", "--paths", "40"]) == 0
    printed = capsys.readouterr().out
    assert "target 80%" in printed and "Highest spending" in printed and "Earliest retirement" in printed
    assert "Partner A: CPP" in printed and "Expected legacy (over lifespans)" in printed
    assert "RRSP draw for most legacy" in printed and "least lifetime tax" in printed
    assert not out.exists()


def test_the_report_compares_saved_scenarios(tmp_path, monkeypatch):
    monkeypatch.setattr(holdings, "load", lambda *a, **k: pytest.fail("holdings must not be read"))
    d = copy.deepcopy(inputs.TEMPLATE)
    d["saved_scenarios"] = [{"name": "Spend 10% less", "changes": {"spending.base": 72_000}}]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(d))
    out = tmp_path / "out"
    assert cli.main(["retire", "--inputs", str(path), "--out", str(out), *FAST]) == 0
    [run_dir] = list(out.iterdir())
    assert "Spend 10% less" in (run_dir / "retirement_report.html").read_text()
