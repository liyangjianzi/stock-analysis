"""Tests for the `stock-analysis thesis` CLI subcommand (offline).

Drives the real argparse dispatch via cli.main(); state goes to a tmp dir and
postmortem uses --no-prices so nothing hits the network.
"""
from __future__ import annotations

import pandas as pd

from stockanalysis import cli
from stockanalysis.thesis import store


def _register(tmp, capsys, ticker="AAPL"):
    rc = cli.main(["thesis", "register", "--state-dir", str(tmp), "--ticker", ticker,
                   "--type", "long_term_value", "--statement", "Quality compounder.",
                   "--target-price", "200"])
    assert rc == 0
    return capsys.readouterr().out.strip().split()[-1]


def test_register_creates_thesis(tmp_path, capsys):
    tid = _register(tmp_path, capsys)
    th = store.get(tmp_path, tid)
    assert th["ticker"] == "AAPL" and th["status"] == "IDEA"


def test_full_lifecycle_through_cli(tmp_path, capsys):
    tid = _register(tmp_path, capsys)
    sd = str(tmp_path)
    assert cli.main(["thesis", "transition", tid, "ENTRY_READY", "--state-dir", sd,
                     "--reason", "validated"]) == 0
    assert cli.main(["thesis", "open", tid, "--state-dir", sd, "--price", "198",
                     "--date", "2026-06-01", "--shares", "10"]) == 0
    assert cli.main(["thesis", "close", tid, "--state-dir", sd, "--reason", "target_hit",
                     "--price", "230", "--date", "2026-06-29"]) == 0
    capsys.readouterr()
    assert cli.main(["thesis", "postmortem", tid, "--state-dir", sd, "--no-prices"]) == 0
    pm_path = capsys.readouterr().out.strip().split()[-1]
    assert (tmp_path / "journal" / f"pm_{tid}.md").exists()
    assert pm_path.endswith(".md")

    th = store.get(tmp_path, tid)
    assert th["status"] == "CLOSED"
    assert th["outcome"]["pnl_dollars"] == 320.0          # (230-198)*10

    assert cli.main(["thesis", "summary", "--state-dir", sd]) == 0
    out = capsys.readouterr().out
    assert "count" in out.lower() or "1" in out


def test_list_shows_ticker(tmp_path, capsys):
    _register(tmp_path, capsys, ticker="KO")
    assert cli.main(["thesis", "list", "--state-dir", str(tmp_path)]) == 0
    assert "KO" in capsys.readouterr().out


def test_ingest_from_run_reads_signal_matrix(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sm = pd.DataFrame({
        "Ticker": ["AAPL", "XYZ"],
        "Sector": ["Technology", "Energy"],
        "Fundamental Score": [6, 2],
        "Technical Posture": ["Bullish", "Bearish"],
        "Tech Score": [6, 1],
        "Composite": [0.78, 0.23],
        "Final Action Signal": ["Buy", "Watch"],
    })
    sm.to_excel(run_dir / "signal_matrix.xlsx", sheet_name="Signal Matrix", index=False)

    state = tmp_path / "state"
    rc = cli.main(["thesis", "ingest", "--from-run", str(run_dir),
                   "--state-dir", str(state)])
    assert rc == 0
    tickers = {t["ticker"] for t in store.query(state)}
    assert tickers == {"AAPL"}                              # only the Buy row ingested
