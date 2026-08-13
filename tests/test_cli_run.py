# tests/test_cli_run.py
from __future__ import annotations

from unittest import mock

from stockanalysis import cli
from stockanalysis.pipeline import Results


def _run_cli(argv):
    """Invoke the CLI with pipeline.run mocked out; return (rc, call kwargs)."""
    results = Results(chart_paths=["out/AAA.html"],
                      profile_paths=["out/AAA_profile.txt"],
                      run_dir="out")
    with mock.patch("stockanalysis.pipeline.run", return_value=results) as m:
        rc = cli.main(argv)
    return rc, m.call_args.kwargs


def test_cli_run_forwards_top_and_profiles_flags():
    rc, kwargs = _run_cli(["run", "--target", "none", "--top", "5", "--profiles"])

    assert rc == 0
    assert kwargs["top_n"] == 5
    assert kwargs["save_profiles"] is True
    assert kwargs["save_charts"] is True


def test_cli_run_defaults_cover_every_ticker_and_skip_profiles():
    rc, kwargs = _run_cli(["run", "--target", "none"])

    assert rc == 0
    assert kwargs["top_n"] is None          # None -> every screened ticker
    assert kwargs["save_profiles"] is False  # opt-in: one extra fetch per ticker


def test_cli_run_can_write_profiles_without_charts():
    _, kwargs = _run_cli(["run", "--target", "none", "--no-charts", "--profiles"])

    assert kwargs["save_charts"] is False
    assert kwargs["save_profiles"] is True


def test_cli_run_reports_written_profiles(capsys):
    _run_cli(["run", "--target", "none", "--profiles"])

    out = capsys.readouterr().out
    assert "Profiles: 1 report(s)" in out
