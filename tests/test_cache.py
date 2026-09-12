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


# --- merge / restatement detection ---------------------------------------------

def test_merge_prefers_the_freshly_fetched_row_on_duplicate_dates():
    cached = bars(start="2024-01-01", n=5)
    fresh = cached.tail(2) * 2.0

    merged = cache._merge(cached, fresh)

    assert len(merged) == 5
    assert merged["Close"].iloc[-1] == cached["Close"].iloc[-1] * 2.0


def test_merge_appends_new_bars_and_keeps_the_index_sorted_and_unique():
    cached = bars(start="2024-01-01", n=5)
    fresh = bars(start="2024-01-08", n=3, first_close=200.0)

    merged = cache._merge(cached, fresh)

    assert len(merged) == 8
    assert merged.index.is_monotonic_increasing
    assert merged.index.is_unique


def test_is_restated_is_false_when_overlapping_closes_match():
    cached = bars(start="2024-01-01", n=5)

    assert cache._is_restated(cached, cached.tail(3)) is False


def test_is_restated_is_true_after_a_split_halves_history():
    cached = bars(start="2024-01-01", n=5)
    restated = cached.tail(3) / 2.0          # a 2:1 split restates older bars

    assert cache._is_restated(cached, restated) is True


def test_is_restated_ignores_floating_point_noise():
    cached = bars(start="2024-01-01", n=5)
    jittered = cached.tail(3).copy()
    jittered["Close"] = jittered["Close"] * (1 + 1e-9)

    assert cache._is_restated(cached, jittered) is False


def test_is_restated_is_false_without_overlapping_dates():
    # Nothing to compare means nothing to conclude — merge rather than rebuild.
    cached = bars(start="2024-01-01", n=5)
    disjoint = bars(start="2024-02-01", n=3)

    assert cache._is_restated(cached, disjoint) is False


# --- cached_history orchestration ----------------------------------------------

class FakeFetcher:
    """Records every call and returns queued responses.

    Asserting on ``.calls`` is the point: the only proof a fetch was actually
    incremental is the arguments it was made with, not the frame it returned.
    Queue an Exception instance to make that call raise.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, ticker, period=None, start=None):
        self.calls.append({"ticker": ticker, "period": period, "start": start})
        if not self.responses:
            raise AssertionError(f"unexpected extra fetch: {ticker} {period} {start}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_cold_miss_fetches_complete_history_and_writes_the_cache(tmp_path):
    full = bars(start="2024-01-01", n=30)
    fetcher = FakeFetcher(full)

    cache.cached_history("AAPL", "5d", fetcher, cache_dir=tmp_path)

    # 'max', NOT the caller's '5d' — that is the complete-history invariant.
    assert fetcher.calls == [{"ticker": "AAPL", "period": "max", "start": None}]
    assert cache.cache_path("AAPL", cache_dir=tmp_path).exists()


def test_cold_miss_returns_only_the_requested_slice(tmp_path):
    full = recent_bars(n=30)          # must end today — see recent_bars' docstring
    fetcher = FakeFetcher(full)

    out = cache.cached_history("AAPL", "5d", fetcher, cache_dir=tmp_path)

    assert len(out) < len(full)
    assert out.index.max() == full.index.max()


def test_warm_hit_requests_only_the_tail(tmp_path):
    cached = bars(start="2024-01-01", n=20)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    fetcher = FakeFetcher(cached.tail(2))

    cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    call = fetcher.calls[0]
    assert call["period"] is None
    assert call["start"] == cached.index.max() - pd.Timedelta(days=cache.OVERLAP_DAYS)


def test_warm_hit_merges_new_bars_into_the_stored_cache(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    tail = bars(start="2024-01-15", n=3, first_close=300.0)
    fetcher = FakeFetcher(tail)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert len(out) == 13
    assert len(cache.read_cache("AAPL", cache_dir=tmp_path)) == 13


def test_max_caller_is_served_from_a_cache_built_by_a_3y_caller(tmp_path):
    """The regression the complete-history invariant exists to prevent:
    thesis.review asks for 'max' on every run and must not refetch it."""
    full = recent_bars(n=30)          # must end today — see recent_bars' docstring
    cold = FakeFetcher(full)
    cache.cached_history("AAPL", "3y", cold, cache_dir=tmp_path)

    warm = FakeFetcher(full.tail(2))
    out = cache.cached_history("AAPL", "max", warm, cache_dir=tmp_path)

    assert len(warm.calls) == 1                  # the tail top-up only
    assert warm.calls[0]["period"] is None       # never a second full download
    assert len(out) == 30


def test_restated_closes_trigger_a_full_rebuild(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    restated_tail = cached.tail(3) / 2.0          # a 2:1 split
    rebuilt = bars(start="2024-01-01", n=12, first_close=50.0)
    fetcher = FakeFetcher(restated_tail, rebuilt)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert fetcher.calls[1]["period"] == "max"
    assert len(out) == 12
    # The stale pre-split bars are gone, not merged alongside the new ones.
    pd.testing.assert_frame_equal(
        cache.read_cache("AAPL", cache_dir=tmp_path), rebuilt, check_freq=False)


def test_failed_top_up_refetches_complete_history(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    rebuilt = bars(start="2024-01-01", n=14)
    fetcher = FakeFetcher(RuntimeError("connection reset"), rebuilt)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert fetcher.calls[1]["period"] == "max"
    assert len(out) == 14


def test_empty_top_up_response_also_refetches_complete_history(tmp_path):
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    rebuilt = bars(start="2024-01-01", n=14)
    fetcher = FakeFetcher(bars(n=0), rebuilt)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert len(out) == 14


def test_failed_top_up_and_failed_rebuild_serve_the_cached_bars(tmp_path):
    """Fully offline with a warm cache: real, slightly stale output beats none."""
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    fetcher = FakeFetcher(RuntimeError("offline"), RuntimeError("offline"))

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    pd.testing.assert_frame_equal(out, cached, check_freq=False)


def test_failed_cold_max_falls_back_to_the_requested_period_unwritten(tmp_path):
    partial = bars(start="2024-01-01", n=5)
    fetcher = FakeFetcher(RuntimeError("max unavailable"), partial)

    out = cache.cached_history("AAPL", "3y", fetcher, cache_dir=tmp_path)

    assert fetcher.calls[1]["period"] == "3y"
    pd.testing.assert_frame_equal(out, partial, check_freq=False)
    # Never store a partial fetch — that is what keeps coverage metadata-free.
    assert not cache.cache_path("AAPL", cache_dir=tmp_path).exists()


def test_returns_none_when_every_fetch_fails_with_no_cache(tmp_path):
    fetcher = FakeFetcher(RuntimeError("offline"), RuntimeError("offline"))

    assert cache.cached_history("AAPL", "3y", fetcher, cache_dir=tmp_path) is None


def test_intraday_move_on_todays_cached_bar_does_not_trigger_a_rebuild(tmp_path):
    """A same-day bar's close can still move between two runs made minutes
    apart during market hours — that is not a split/dividend restatement.
    Comparing it against the freshly re-fetched value would flag every
    same-day rerun as 'restated' and force a full 'max' re-download, which is
    slower than the pre-cache code in exactly the scenario the cache exists
    for. The newest cached bar must be excluded from the overlap comparison."""
    cached = recent_bars(n=10)          # last bar is dated today
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    today = cached.index.max()
    moved_today = cached.tail(1).copy()
    moved_today["Close"] = moved_today["Close"] * 1.003   # ticked up intraday

    fetcher = FakeFetcher(moved_today)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    # Only the tail top-up — no 'max' rebuild triggered by the intraday move.
    assert fetcher.calls == [
        {"ticker": "AAPL", "period": None,
         "start": today - pd.Timedelta(days=cache.OVERLAP_DAYS)}
    ]
    assert out.loc[today, "Close"] == moved_today["Close"].iloc[0]
    assert len(cache.read_cache("AAPL", cache_dir=tmp_path)) == 10


def test_write_cache_normalizes_a_tz_aware_dst_spanning_index(tmp_path):
    """overview.fetch_index_data (yf.download) returns tz-aware bars, unlike
    ingest._raw_history which already strips tz before the cache sees it. If a
    tz-aware index spans a DST boundary, the serialized CSV carries mixed UTC
    offsets ('-04:00' before the fall-back, '-05:00' after); pd.to_datetime
    on read then raises, read_cache treats it as a miss, and the same broken
    shape gets rewritten on every run — a silent permanent-miss loop that only
    appears once the cached range crosses a DST change. write_cache must
    normalize to tz-naive on ingress rather than relying on read-side
    normalization alone."""
    idx = pd.date_range("2024-10-30", "2024-11-05", freq="D", tz="America/New_York")
    df = _frame(idx, 100.0)

    cache.write_cache("AAPL", df, cache_dir=tmp_path)
    loaded = cache.read_cache("AAPL", cache_dir=tmp_path)

    assert loaded is not None
    expected = df.copy()
    expected.index = idx.tz_localize(None)
    expected.index.name = "Date"
    pd.testing.assert_frame_equal(loaded, expected, check_freq=False)


def test_warm_hit_skips_the_write_when_the_merge_is_a_no_op(tmp_path, monkeypatch):
    """After hours, a tail fetch that returns exactly what's already cached
    produces a merge identical to the stored frame — rewriting ~490 KB of CSV
    for nothing. The write should be skipped when nothing changed."""
    cached = bars(start="2024-01-01", n=10)
    cache.write_cache("AAPL", cached, cache_dir=tmp_path)
    unchanged_tail = cached.tail(2)
    fetcher = FakeFetcher(unchanged_tail)

    write_calls = []
    original_write_cache = cache.write_cache

    def spy(*args, **kwargs):
        write_calls.append(args)
        return original_write_cache(*args, **kwargs)

    monkeypatch.setattr(cache, "write_cache", spy)

    out = cache.cached_history("AAPL", "max", fetcher, cache_dir=tmp_path)

    assert write_calls == []
    pd.testing.assert_frame_equal(out, cached, check_freq=False)
