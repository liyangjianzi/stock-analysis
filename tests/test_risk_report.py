"""Tests for the household risk report (report.build_risk_report) and the
``stock-analysis risk`` command — offline, with an invented portfolio."""
from __future__ import annotations

import csv

import numpy as np
import pandas as pd
import pytest

from datetime import datetime

from stockanalysis import cli, portfolio, report
from stockanalysis.holdings import FRAME_COLUMNS
from stockanalysis.outputs.base import SIGNAL_MATRIX_SHEET
from stockanalysis.pipeline import RUN_DIR_FMT

HEADER = ["Column 1", "Ticker", "Shares", "Current Price", "Purchase Price",
          "Total Value ", "Total Value CAD", "Total Cost", "Gain/Loss",
          "Gain/Loss CAD", "Currency", "Gain/Loss (%)", "Weight"]


def _holdings():
    return pd.DataFrame([
        ["A USD", "AAA", "AAA", "stock", 30, 100.0, 50.0, "USD", 3000.0, 4200.0],
        ["B CAD", "BBB", "BBB.TO", "stock", 100, 30.0, 20.0, "CAD", 3000.0, 3000.0],
        ["A USD", "Cash_USD", None, "cash", np.nan, np.nan, np.nan, "USD", 1000.0, 1400.0],
        ["B CAD", "Cash_CAD", None, "cash", np.nan, np.nan, np.nan, "CAD", 1400.0, 1400.0],
    ], columns=FRAME_COLUMNS)                                   # total 10,000 CAD


def _market():
    idx = pd.bdate_range("2017-01-02", "2026-09-25")
    rng = np.random.default_rng(3)
    walk = lambda: pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx)
    return {"info": {"AAA": {"sector": "Technology", "name": "Aaa Inc."},
                     "BBB.TO": {"sector": "Index fund", "name": "Bbb Index ETF"}},
            "closes_cad": pd.DataFrame({"AAA": walk(), "BBB.TO": walk()}),
            "bench_cad": pd.DataFrame({"S&P 500": walk()}),
            "tech": {}, "usdcad": 1.4, "proxies": {}, "fx": {"USD": pd.Series(1.35, index=idx)}}


def _risk(**kw):
    history = pd.Series([100.0, 90.0, 99.0],
                        index=pd.to_datetime(["2026-09-23", "2026-09-24", "2026-09-25"]))
    kw.setdefault("history", history)
    kw.setdefault("contributions", pd.Series(dtype=float))
    return portfolio.build_risk(_holdings(), _market(), **kw)


def _doc(risk=None, **kw):
    return report.build_risk_report(risk or _risk(), generated_at="now",
                                    source="snapshot (test)", **kw)


def _matrix(action="Buy"):
    return pd.DataFrame({"Ticker": ["ZZZ"], "Final Action Signal": [action],
                         "Shares": [5], "Entry": [100.0]})


def _funding(doc):
    return doc[doc.index('id="funding"'):]


def test_risk_report_has_every_section_in_the_nav():
    doc = _doc()
    for anchor in ("exposure", "market", "stress", "drawdown", "stops", "funding"):
        assert f'id="{anchor}"' in doc and f'href="#{anchor}"' in doc


def test_risk_report_headlines_total_source_and_flags():
    doc = _doc()
    assert "C$10,000" in doc
    assert "snapshot (test)" in doc
    assert "AAA is 42.0% of the portfolio" in doc               # > 10% limit, flagged


def test_risk_report_warns_that_index_funds_overlap_direct_holdings():
    assert "Index fund" in _doc() and "overlap" in _doc()


def test_risk_report_shows_uncovered_stress_names():
    risk = _risk()
    risk["stress"][0]["missing"] = ["AAA"]
    assert "not in the data: AAA" in _doc(risk)


def test_risk_report_without_history_says_so():
    assert "No account history" in _doc(_risk(history=None))


def test_risk_report_lists_buys_and_who_can_fund_them():
    section = _funding(_doc(_risk(signal_matrix=_matrix()), signals_source="run 2026-09-28_170000"))
    assert "ZZZ" in section and "A USD" in section
    assert "run 2026-09-28_170000" in section                    # which run was checked


def test_risk_report_stamps_it_as_display_only():
    assert "not an input to any signal" in _doc()


# --- CLI ---------------------------------------------------------------------------------

def _write_csv(path):
    rows = [HEADER,
            ["A USD", "AAA", "30", "100", "50", "3,000", "4,200", "", "", "", "USD", "", ""],
            ["B CAD", "BBB", "100", "30", "20", "3,000", "3,000", "", "", "", "CAD", "", ""],
            ["A USD", "Cash_USD", "", "", "", "1,000", "1,400", "", "", "", "USD", "", ""],
            ["B CAD", "Cash_CAD", "", "", "", "1,400", "1,400", "", "", "", "CAD", "", ""]]
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


@pytest.fixture
def holdings_csv(tmp_path, monkeypatch):
    """An invented holdings export, with the market fetch stubbed out."""
    monkeypatch.setattr(portfolio, "fetch_market", lambda h, **k: _market())
    src = tmp_path / "holdings_snapshot.csv"
    _write_csv(src)
    return src


def _is_run_dir(path) -> bool:
    try:
        datetime.strptime(path.name, RUN_DIR_FMT)
        return True
    except ValueError:
        return False


def test_risk_command_writes_the_report(holdings_csv, tmp_path, capsys):
    assert cli.main(["risk", "--holdings", str(holdings_csv), "--out", str(tmp_path / "out")]) == 0
    written = list((tmp_path / "out").glob("*/risk_report.html"))
    assert len(written) == 1 and _is_run_dir(written[0].parent)
    out = capsys.readouterr().out
    assert "C$10,000" in out and "risk_report.html" in out


def test_a_standalone_risk_run_goes_under_output_risk(holdings_csv, tmp_path, monkeypatch):
    """Its own folder: output/<ts>/ is for pipeline runs, not a lone risk report."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["risk", "--holdings", str(holdings_csv)]) == 0
    written = list((tmp_path / "output" / "risk").glob("*/risk_report.html"))
    assert len(written) == 1 and _is_run_dir(written[0].parent)


def test_risk_from_run_joins_that_run_and_checks_its_buys(holdings_csv, tmp_path):
    run_dir = tmp_path / "output" / "2026-09-28_170000"
    run_dir.mkdir(parents=True)
    _matrix().to_excel(run_dir / "signal_matrix.xlsx", sheet_name=SIGNAL_MATRIX_SHEET, index=False)
    assert cli.main(["risk", "--holdings", str(holdings_csv), "--from-run", str(run_dir)]) == 0
    doc = (run_dir / "risk_report.html").read_text(encoding="utf-8")
    assert "ZZZ" in _funding(doc) and "run 2026-09-28_170000" in _funding(doc)


def test_risk_command_without_holdings_fails_clearly(tmp_path, capsys):
    rc = cli.main(["risk", "--holdings", str(tmp_path / "missing.csv"), "--out", str(tmp_path)])
    assert rc == 1
    assert "holdings" in capsys.readouterr().err.lower()


def test_a_checked_run_without_buys_says_which_run():
    section = _funding(_doc(_risk(signal_matrix=_matrix("Hold")),
                            signals_source="run 2026-09-28_170000"))
    assert "No Buy signals in run 2026-09-28_170000" in section and "--from-run" not in section


def test_no_signal_matrix_points_at_the_ways_to_check_one():
    section = _funding(_doc())
    assert "--risk" in section and "--from-run" in section
