"""Tests for config.load_watchlist_csv (the runtime watchlist loader).

Each test writes to a distinct tmp_path so the lru_cache on _read_watchlist_csv
(keyed by resolved path) never serves a stale result between tests.
"""
from __future__ import annotations

import pytest

from stockanalysis import config


def _write(tmp_path, text):
    p = tmp_path / "watchlist.csv"
    p.write_text(text)
    return p


def test_loads_ticker_to_sector_mapping(tmp_path):
    p = _write(tmp_path, "ticker,sector\nAAPL,Technology\nJPM,Financials\n")
    assert config.load_watchlist_csv(str(p)) == {
        "AAPL": "Technology", "JPM": "Financials",
    }


def test_whitespace_stripped_and_blank_tickers_skipped(tmp_path):
    p = _write(tmp_path, "ticker,sector\n  AAPL ,  Technology \n,Financials\n")
    out = config.load_watchlist_csv(str(p))
    assert out == {"AAPL": "Technology"}  # blank-ticker row dropped


def test_extra_columns_are_ignored(tmp_path):
    # Guards the 4-column data/watchlist.csv (ticker,company,exchange,sector).
    p = _write(
        tmp_path,
        "ticker,company,exchange,sector\nAAPL,Apple,NASDAQ,Technology\n",
    )
    assert config.load_watchlist_csv(str(p)) == {"AAPL": "Technology"}


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.load_watchlist_csv(str(tmp_path / "does_not_exist.csv"))


def test_default_watchlist_loads_real_csv():
    # No arg -> reads the shipped data/watchlist.csv. Assert loader invariants,
    # not the exact contents: that file is meant to be user-edited, so coupling
    # the test to specific tickers/counts would make a data edit break a code test.
    out = config.load_watchlist_csv()
    assert isinstance(out, dict) and out  # non-empty -> default path resolved + parsed
    assert all(isinstance(t, str) and t for t in out)         # tickers are non-empty
    assert all(isinstance(s, str) and s for s in out.values())  # sectors are non-empty
