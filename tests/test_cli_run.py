# tests/test_cli_run.py
from __future__ import annotations

from unittest import mock

from stockanalysis import cli, config, signals
from stockanalysis.pipeline import Results


def _run_cli(argv):
    """Invoke the CLI with pipeline.run mocked out; return (rc, call kwargs)."""
    results = Results(report_path="out/report.html", run_dir="out")
    with mock.patch("stockanalysis.pipeline.run", return_value=results) as m:
        rc = cli.main(argv)
    return rc, m.call_args.kwargs


def test_cli_run_forwards_top_flag():
    rc, kwargs = _run_cli(["run", "--target", "none", "--top", "3"])

    assert rc == 0
    assert kwargs["top_n"] == 3
    assert kwargs["save_report"] is True


def test_cli_run_defaults_top_5_and_report_on():
    rc, kwargs = _run_cli(["run", "--target", "none"])

    assert rc == 0
    assert kwargs["top_n"] == 5
    assert kwargs["save_report"] is True


def test_cli_run_no_report_opts_out():
    _, kwargs = _run_cli(["run", "--target", "none", "--no-report"])

    assert kwargs["save_report"] is False


def test_cli_run_reports_the_written_report_path(capsys):
    _run_cli(["run", "--target", "none"])

    out = capsys.readouterr().out
    assert "Report: out/report.html" in out


def test_cli_run_forwards_risk_flags_converting_percent_to_fraction():
    """The CLI takes percents (1.0 = 1%), the library API takes fractions."""
    rc, kwargs = _run_cli(["run", "--target", "none", "--account", "50000",
                           "--risk-pct", "0.5", "--max-weight", "10",
                           "--fund-min", "5"])

    assert rc == 0
    assert kwargs["account_size"] == 50_000
    assert kwargs["risk_pct"] == 0.005
    assert kwargs["max_weight"] == 0.10
    assert kwargs["fund_min"] == 5


def test_cli_run_risk_defaults_match_config():
    rc, kwargs = _run_cli(["run", "--target", "none"])

    assert rc == 0
    assert kwargs["account_size"] == config.DEFAULT_ACCOUNT_SIZE
    assert kwargs["risk_pct"] == config.DEFAULT_RISK_PCT
    assert kwargs["max_weight"] == config.DEFAULT_MAX_WEIGHT
    assert kwargs["fund_min"] == signals.DEFAULT_FUND_MIN
