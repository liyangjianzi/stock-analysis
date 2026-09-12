"""Tests for the local price-history cache — fully offline.

No network and no yfinance: cache.py takes a fetcher callable, so every test
either exercises pure file I/O or injects a recording fake fetcher. Every test
writes under pytest's tmp_path, never the real data/cache/ directory.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from stockanalysis import cache


def _frame(idx: pd.DatetimeIndex, first_close: float) -> pd.DataFrame:
    idx.name = "Date"
    close = pd.Series([first_close + i for i in range(len(idx))], index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": 1_000_000},
        index=idx,
    )


def bars(start: str = "2024-01-02", n: int = 5, first_close: float = 100.0) -> pd.DataFrame:
    """A tiny tz-naive OHLCV frame on business days, closes ascending by 1."""
    return _frame(pd.bdate_range(start, periods=n), first_close)


def recent_bars(n: int = 30, first_close: float = 100.0) -> pd.DataFrame:
    """Bars ENDING TODAY.

    Required by any test that exercises period slicing: ``cache._slice``
    measures its cutoff from today, not from the frame's last bar. Asking a
    2024-anchored fixture for '5d' correctly returns an empty frame — which is
    right behaviour and a wrong test.
    """
    return _frame(pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n),
                  first_close)


# --- storage primitives --------------------------------------------------------

def test_write_then_read_round_trips_bars(tmp_path):
    df = bars()

    cache.write_cache("AAPL", df, cache_dir=tmp_path)
    loaded = cache.read_cache("AAPL", cache_dir=tmp_path)

    pd.testing.assert_frame_equal(loaded, df, check_freq=False)


def test_read_cache_returns_none_when_file_missing(tmp_path):
    assert cache.read_cache("NOPE", cache_dir=tmp_path) is None


def test_read_cache_returns_none_for_corrupt_file(tmp_path):
    path = cache.cache_path("AAPL", cache_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this,is not\na valid\x00 csv frame")

    # A corrupt cache is a miss, never an exception.
    assert cache.read_cache("AAPL", cache_dir=tmp_path) is None


def test_read_cache_returns_none_for_empty_frame(tmp_path):
    cache.write_cache("AAPL", bars(n=0), cache_dir=tmp_path)

    assert cache.read_cache("AAPL", cache_dir=tmp_path) is None


def test_cache_path_sanitizes_unsafe_characters(tmp_path):
    # '^' (index symbols) and '/' must never reach the filesystem as-is.
    path = cache.cache_path("^GSPC", cache_dir=tmp_path)

    assert path.name == "_GSPC.csv"
    assert path.parent == tmp_path


def test_cache_path_keeps_dots_so_tsx_tickers_stay_readable(tmp_path):
    assert cache.cache_path("su.to", cache_dir=tmp_path).name == "SU.TO.csv"


def test_write_cache_leaves_no_temp_files_behind(tmp_path):
    cache.write_cache("AAPL", bars(), cache_dir=tmp_path)

    assert [p.name for p in tmp_path.iterdir()] == ["AAPL.csv"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores directory permissions")
def test_write_cache_returns_false_when_directory_is_unwritable(tmp_path):
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        # A read-only disk costs speed, never correctness: warn and move on.
        assert cache.write_cache("AAPL", bars(), cache_dir=ro / "prices") is False
    finally:
        ro.chmod(0o700)


# --- period parsing / slicing --------------------------------------------------

_TODAY = pd.Timestamp("2026-09-11")


def test_period_start_parses_years_months_weeks_and_days():
    # Deliberately generous units: a year counts as 366 days, a month as 31.
    assert cache._period_start("3y", today=_TODAY) == _TODAY - pd.Timedelta(days=1098)
    assert cache._period_start("6mo", today=_TODAY) == _TODAY - pd.Timedelta(days=186)
    assert cache._period_start("2wk", today=_TODAY) == _TODAY - pd.Timedelta(days=14)
    assert cache._period_start("5d", today=_TODAY) == _TODAY - pd.Timedelta(days=5)


def test_period_start_is_none_for_max():
    assert cache._period_start("max", today=_TODAY) is None


def test_period_start_is_none_for_unrecognized_input():
    # Unknown input must fail SAFE: no cutoff means the caller gets every bar
    # we have, never fewer than it asked for.
    assert cache._period_start("banana", today=_TODAY) is None
    assert cache._period_start(None, today=_TODAY) is None


def test_period_start_resolves_ytd_to_january_first():
    assert cache._period_start("ytd", today=_TODAY) == pd.Timestamp("2026-01-01")


def test_slice_trims_to_the_requested_period():
    df = bars(start="2024-01-01", n=60)
    today = df.index.max()

    out = cache._slice(df, "5d", today=today)

    assert out.index.min() >= today - pd.Timedelta(days=5)
    assert out.index.max() == today


def test_slice_returns_everything_for_max():
    df = bars(start="2024-01-01", n=60)

    pd.testing.assert_frame_equal(cache._slice(df, "max", today=df.index.max()), df)
