"""Tests for pipeline helpers — fully offline (no network / yfinance)."""
from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pandas as pd

from stockanalysis import pipeline
from stockanalysis.pipeline import RUN_DIR_FMT, run_output_dir

# RUN_DIR_FMT is "%Y-%m-%d_%H%M%S" → e.g. 2026-06-25_143022
_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")


def test_run_output_dir_is_timestamped_subdir_of_base():
    base = "output"
    out = run_output_dir(base)

    assert isinstance(out, Path)
    assert out.parent == Path(base)
    assert _RUN_DIR_RE.match(out.name), f"{out.name!r} does not match {RUN_DIR_FMT}"


def test_run_output_dir_preserves_arbitrary_base():
    out = run_output_dir("/tmp/some/where")
    assert out.parent == Path("/tmp/some/where")
    assert _RUN_DIR_RE.match(out.name)


def test_run_output_dir_does_not_create_the_directory():
    """Compute-only: resolving a run dir must not touch the filesystem."""
    out = run_output_dir("output")
    assert not out.exists()


# --- run(): selection + the combined report ------------------------------------

def _stub_run(monkeypatch, tickers, screens=True):
    """Point run() at synthetic data so nothing touches the network.

    ``screens=False`` drops every ticker at the screener, which is how an
    offline run behaves: prices/indicators exist but the signal matrix is empty.
    """
    screened = pd.DataFrame(
        {"Fundamental_Score": range(len(tickers), 0, -1),   # descending -> stable rank
         "Sector": ["Technology"] * len(tickers)},
        index=list(tickers),
    )
    screened.index.name = "Ticker"
    monkeypatch.setattr(pipeline, "load_watchlist",
                        lambda wl, period=None: ({t: pd.DataFrame() for t in tickers}, screened))
    monkeypatch.setattr(pipeline, "screen_fundamentals",
                        lambda df: df if screens else pd.DataFrame())
    monkeypatch.setattr(pipeline, "compute_indicators",
                        lambda prices: {t: pd.DataFrame() for t in tickers})
    monkeypatch.setattr(pipeline.profile, "build_profile",
                        lambda ticker, screened_df=None: {"report": f"report for {ticker}"})
    monkeypatch.setattr(pipeline.overview, "daily_overview",
                        lambda watchlist, signal_matrix, tech: {
                            "index_data": {}, "indices": [], "vix": None,
                            "headlines": [], "candidates": pd.DataFrame(), "action_plan": {},
                        })

    def fake_build_full_report(screened_df, signal_matrix, tech, profiles,
                               overview_data, *, selected, generated_at):
        return f"<html>{selected}</html>"

    monkeypatch.setattr(pipeline.report, "build_full_report", fake_build_full_report)

    def fake_save_report(html_doc, path):                # mirrors report.save_report
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_doc)
        return str(path)

    monkeypatch.setattr(pipeline.report, "save_report", fake_save_report)


def _run_capturing_selected(tickers, **run_kwargs):
    """Run the (already-stubbed) pipeline and return (results, selected) —
    ``selected`` is the ticker list report.build_full_report was actually
    called with, letting tests assert the top_n/fallback selection logic
    without depending on chart/profile file side effects."""
    with mock.patch("stockanalysis.pipeline.report.build_full_report",
                    wraps=pipeline.report.build_full_report) as m:
        results = pipeline.run(watchlist={t: "Technology" for t in tickers}, **run_kwargs)
    return results, m.call_args.kwargs["selected"]


def test_run_top_n_limits_the_report_selection(monkeypatch, tmp_path):
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    _stub_run(monkeypatch, tickers)

    results, selected = _run_capturing_selected(tickers, top_n=2, out_dir=str(tmp_path))

    assert selected == results.signal_matrix["Ticker"].tolist()[:2]
    assert Path(results.report_path).exists()


def test_run_writes_the_report_into_the_reported_run_dir(monkeypatch, tmp_path):
    """Results.run_dir is the folder callers should reuse — everything lands there."""
    tickers = ["AAA", "BBB"]
    _stub_run(monkeypatch, tickers)

    results = pipeline.run(watchlist={t: "Technology" for t in tickers},
                           out_dir=str(tmp_path))

    run_dir = Path(results.run_dir)
    assert run_dir.parent == tmp_path
    assert Path(results.report_path).parent == run_dir


def test_run_falls_back_to_fetched_tickers_when_nothing_screens(monkeypatch, tmp_path):
    """Offline/empty-matrix path: the matrix has no rows (and no columns), so the
    selection falls back to whatever was fetched — still honouring top_n."""
    tickers = ["AAA", "BBB", "CCC"]
    _stub_run(monkeypatch, tickers, screens=False)

    results, selected = _run_capturing_selected(tickers, top_n=2, out_dir=str(tmp_path))

    assert results.signal_matrix.empty
    assert selected == tickers[:2]


def test_run_default_top_n_is_five(monkeypatch, tmp_path):
    tickers = [f"T{i}" for i in range(8)]
    _stub_run(monkeypatch, tickers)

    results, selected = _run_capturing_selected(tickers, out_dir=str(tmp_path))

    assert len(selected) == 5
    assert Path(results.report_path).exists()


def test_run_skips_the_report_unless_requested(monkeypatch, tmp_path):
    tickers = ["AAA", "BBB"]
    _stub_run(monkeypatch, tickers)

    with mock.patch("stockanalysis.pipeline.report.build_full_report") as build, \
         mock.patch("stockanalysis.pipeline.report.save_report") as save:
        results = pipeline.run(watchlist={t: "Technology" for t in tickers},
                               save_report=False, out_dir=str(tmp_path))

    assert results.report_path is None
    build.assert_not_called()
    save.assert_not_called()
