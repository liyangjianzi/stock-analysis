"""Tests for pipeline helpers — fully offline (no network / yfinance)."""
from __future__ import annotations

import re
from pathlib import Path

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


# --- run(): selection, charts and profiles ------------------------------------

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
    monkeypatch.setattr(pipeline.charts, "build_technical_dashboard",
                        lambda ticker, tech: object())
    def fake_save_html(fig, path):                      # mirrors charts.save_html
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html>")
        return str(path)

    monkeypatch.setattr(pipeline.charts, "save_html", fake_save_html)
    monkeypatch.setattr(pipeline.profile, "build_profile",
                        lambda ticker, screened_df=None: {"report": f"report for {ticker}"})


def test_run_top_n_limits_charts_and_profiles(monkeypatch, tmp_path):
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    _stub_run(monkeypatch, tickers)

    results = pipeline.run(watchlist={t: "Technology" for t in tickers},
                           save_charts=True, save_profiles=True, top_n=2,
                           out_dir=str(tmp_path))

    expected = results.signal_matrix["Ticker"].tolist()[:2]
    assert [Path(p).stem for p in results.chart_paths] == expected
    assert [Path(p).stem for p in results.profile_paths] == [f"{t}_profile" for t in expected]
    for path in results.profile_paths:
        assert Path(path).read_text(encoding="utf-8").startswith("report for ")


def test_run_writes_every_artifact_into_the_reported_run_dir(monkeypatch, tmp_path):
    """Results.run_dir is the folder callers should reuse — everything lands there."""
    tickers = ["AAA", "BBB"]
    _stub_run(monkeypatch, tickers)

    results = pipeline.run(watchlist={t: "Technology" for t in tickers},
                           save_charts=True, save_profiles=True, out_dir=str(tmp_path))

    run_dir = Path(results.run_dir)
    assert run_dir.parent == tmp_path
    for path in results.chart_paths + results.profile_paths:
        assert Path(path).parent == run_dir


def test_run_falls_back_to_fetched_tickers_when_nothing_screens(monkeypatch, tmp_path):
    """Offline/empty-matrix path: the matrix has no rows (and no columns), so the
    selection falls back to whatever was fetched — still honouring top_n."""
    tickers = ["AAA", "BBB", "CCC"]
    _stub_run(monkeypatch, tickers, screens=False)

    results = pipeline.run(watchlist={t: "Technology" for t in tickers},
                           save_charts=True, save_profiles=True, top_n=2,
                           out_dir=str(tmp_path))

    assert results.signal_matrix.empty
    assert [Path(p).stem for p in results.chart_paths] == tickers[:2]
    assert [Path(p).stem for p in results.profile_paths] == [f"{t}_profile" for t in tickers[:2]]


def test_run_without_top_n_covers_every_screened_ticker(monkeypatch, tmp_path):
    tickers = ["AAA", "BBB", "CCC"]
    _stub_run(monkeypatch, tickers)

    results = pipeline.run(watchlist={t: "Technology" for t in tickers},
                           save_charts=True, save_profiles=True, out_dir=str(tmp_path))

    assert sorted(Path(p).stem for p in results.chart_paths) == sorted(tickers)
    assert len(results.profile_paths) == len(tickers)


def test_run_skips_profiles_unless_requested(monkeypatch, tmp_path):
    tickers = ["AAA", "BBB"]
    _stub_run(monkeypatch, tickers)

    results = pipeline.run(watchlist={t: "Technology" for t in tickers},
                           save_charts=True, out_dir=str(tmp_path))

    assert results.chart_paths and results.profile_paths == []
