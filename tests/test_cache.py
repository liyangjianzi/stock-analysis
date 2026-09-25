"""SQLite price cache — round-trip, idempotence, incremental refresh.

Fully offline: every test uses an in-memory database and synthetic frames.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from conftest import wandering_ohlcv

from stockanalysis import cache


@pytest.fixture
def conn():
    c = cache.connect(":memory:")
    yield c
    c.close()


def _frame(n=50, scale=100.0):
    return wandering_ohlcv(scale, n=n)


def test_round_trip_preserves_the_ohlcv_contract(conn):
    df = _frame()
    assert cache.upsert_bars(conn, "AAA", df) == len(df)

    back = cache.load_bars(conn, "AAA")
    assert list(back.columns) == cache.COLUMNS
    assert isinstance(back.index, pd.DatetimeIndex)
    assert len(back) == len(df)
    np.testing.assert_allclose(back["Close"].to_numpy(), df["Close"].to_numpy())


def test_upserting_the_same_window_twice_does_not_duplicate(conn):
    df = _frame()
    cache.upsert_bars(conn, "AAA", df)
    cache.upsert_bars(conn, "AAA", df)              # a refresh with full overlap
    assert len(cache.load_bars(conn, "AAA")) == len(df)


def test_upsert_overwrites_a_revised_bar(conn):
    """Yahoo restates bars; the newer fetch must win, not append."""
    df = _frame()
    cache.upsert_bars(conn, "AAA", df)
    revised = df.copy()
    revised.iloc[-1, revised.columns.get_loc("Close")] = 999.0
    cache.upsert_bars(conn, "AAA", revised)

    back = cache.load_bars(conn, "AAA")
    assert len(back) == len(df)
    assert back["Close"].iloc[-1] == pytest.approx(999.0)


def test_tickers_are_isolated_from_each_other(conn):
    cache.upsert_bars(conn, "AAA", _frame(scale=100.0))
    cache.upsert_bars(conn, "BBB", _frame(scale=500.0))

    assert cache.cached_tickers(conn) == ["AAA", "BBB"]
    assert cache.load_bars(conn, "AAA")["Close"].mean() < 200
    assert cache.load_bars(conn, "BBB")["Close"].mean() > 400


def test_last_date_anchors_an_incremental_refresh(conn):
    df = _frame()
    cache.upsert_bars(conn, "AAA", df)
    assert cache.last_date(conn, "AAA") == pd.Timestamp(df.index[-1].date())
    assert cache.last_date(conn, "NOPE") is None


def test_load_universe_returns_the_dict_the_backtest_consumes(conn):
    cache.upsert_bars(conn, "AAA", _frame())
    cache.upsert_bars(conn, "BBB", _frame())

    uni = cache.load_universe(conn)
    assert set(uni) == {"AAA", "BBB"}
    assert all(list(v.columns) == cache.COLUMNS for v in uni.values())
    assert set(cache.load_universe(conn, ["AAA"])) == {"AAA"}
    # an uncached name is omitted, not returned as an empty frame
    assert cache.load_universe(conn, ["AAA", "GHOST"]).keys() == {"AAA"}


def test_missing_ticker_reads_back_empty_not_raising(conn):
    df = cache.load_bars(conn, "GHOST")
    assert df.empty and list(df.columns) == cache.COLUMNS


@pytest.mark.parametrize("bad", [
    None,
    pd.DataFrame(),
    pd.DataFrame({"Close": [1.0, 2.0]}),          # missing the rest of the contract
], ids=["none", "empty", "partial-columns"])
def test_degenerate_frames_write_nothing(conn, bad):
    assert cache.upsert_bars(conn, "AAA", bad) == 0
    assert cache.cached_tickers(conn) == []


def test_nan_bars_survive_the_round_trip_as_nan(conn):
    """NaN means fail, never crash — a missing bar must not become 0.0."""
    df = _frame()
    df.iloc[3, df.columns.get_loc("Volume")] = np.nan
    cache.upsert_bars(conn, "AAA", df)
    assert np.isnan(cache.load_bars(conn, "AAA")["Volume"].iloc[3])


def test_coverage_reports_span_per_ticker(conn):
    df = _frame()
    cache.upsert_bars(conn, "AAA", df)
    cov = cache.coverage(conn)

    assert list(cov.columns) == ["Ticker", "Bars", "From", "To"]
    assert cov.loc[0, "Bars"] == len(df)
    assert cov.loc[0, "To"] == df.index[-1].strftime("%Y-%m-%d")


def test_cached_frames_feed_add_indicators_unchanged(conn):
    """The whole point: what comes out is what the signal engine can consume."""
    from stockanalysis.indicators import add_indicators
    cache.upsert_bars(conn, "AAA", _frame(n=300))
    enriched = add_indicators(cache.load_bars(conn, "AAA"))
    assert {"EMA50", "ATR14", "RSI3"}.issubset(enriched.columns)
    assert np.isfinite(enriched["ATR14"].iloc[-1])


# --- incremental refresh (the CLI path) ----------------------------------------

def test_cache_cli_only_fetches_full_history_for_new_tickers(tmp_path, monkeypatch):
    """A nightly refresh must top up, not re-download the universe."""
    from stockanalysis import cli

    db = tmp_path / "prices.db"
    uni = tmp_path / "u.csv"
    uni.write_text("ticker,company,exchange,sector\nAAA,A,X,Tech\nBBB,B,X,Tech\n")

    # AAA is already cached; BBB is new.
    with cache.connect(db) as c:
        cache.upsert_bars(c, "AAA", _frame())

    calls = []
    def fake_bulk(tickers, period="10y", chunk=50):
        calls.append((sorted(tickers), period))
        return {t: _frame() for t in tickers}

    monkeypatch.setattr("stockanalysis.ingest.fetch_bulk_prices", fake_bulk)
    rc = cli.main(["cache", "--universe", str(uni), "--db", str(db),
                   "--period", "10y", "--refresh-period", "1mo"])

    assert rc == 0
    assert (["BBB"], "10y") in calls          # new ticker: full history
    assert (["AAA"], "1mo") in calls          # cached ticker: short top-up


def test_cache_cli_full_flag_refetches_everything(tmp_path, monkeypatch):
    from stockanalysis import cli
    db = tmp_path / "prices.db"
    uni = tmp_path / "u.csv"
    uni.write_text("ticker,company,exchange,sector\nAAA,A,X,Tech\n")
    with cache.connect(db) as c:
        cache.upsert_bars(c, "AAA", _frame())

    calls = []
    def fake_bulk(tickers, period="10y", chunk=50):
        calls.append((sorted(tickers), period))
        return {}

    monkeypatch.setattr("stockanalysis.ingest.fetch_bulk_prices", fake_bulk)
    cli.main(["cache", "--universe", str(uni), "--db", str(db), "--full"])
    assert calls == [(["AAA"], "10y")]


# --- adjustment drift ------------------------------------------------------------
# auto_adjust=True rescales *every* bar before a dividend/split ex-date. A top-up
# window rewrites only its own bars, so without a check the cache ends up holding
# two adjustment bases with a step between them (APH, 2026-09-24: x0.998451).

def _readjusted(df, ex_date_pos, factor):
    """What Yahoo serves after a dividend/split: every bar before the ex-date
    rescaled by ``factor`` (volume untouched, as for a cash dividend)."""
    out = df.copy()
    out.iloc[:ex_date_pos, out.columns.get_indexer(["Open", "High", "Low", "Close"])] *= factor
    return out


def test_a_top_up_that_agrees_with_the_cache_is_not_drift(conn):
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist.iloc[:50])
    assert not cache.drifted(conn, "AAA", hist.iloc[40:])


def test_a_dividend_readjustment_inside_the_window_is_drift(conn):
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist.iloc[:50])
    window = _readjusted(hist, ex_date_pos=55, factor=0.998451).iloc[40:]
    assert cache.drifted(conn, "AAA", window)


def test_a_restated_newest_bar_alone_is_not_drift(conn):
    """The newest cached bar may have been fetched intraday; it settling is not a
    re-adjustment and must not force a full refetch every night."""
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist.iloc[:50])
    window = hist.iloc[40:].copy()
    window.iloc[9, window.columns.get_loc("Close")] *= 1.03    # bar 49 = newest cached
    assert not cache.drifted(conn, "AAA", window)


def test_a_window_with_no_overlap_cannot_be_stitched_on(conn):
    """A refresh gap longer than the window leaves nothing to compare against —
    and a hole in the bars. Rebuild the ticker rather than patch it."""
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist.iloc[:30])
    assert cache.drifted(conn, "AAA", hist.iloc[40:])


def test_replace_bars_drops_history_the_new_frame_does_not_cover(conn):
    """A rebuilt ticker must sit on one basis — rows older than the new fetch's
    first bar would be a stale-basis stub."""
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist)
    assert cache.replace_bars(conn, "AAA", hist.iloc[10:] * 0.5) == 50

    back = cache.load_bars(conn, "AAA")
    assert len(back) == 50
    np.testing.assert_allclose(back["Close"].to_numpy(),
                               hist["Close"].iloc[10:].to_numpy() * 0.5)


def test_load_bars_can_start_mid_history(conn):
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist)
    back = cache.load_bars(conn, "AAA", start=hist.index[40])
    assert list(back.index) == list(hist.index[40:])


# --- cache.refresh: the one safe way to write fetched bars ----------------------

def _fake_bulk(monkeypatch, frames, calls):
    """Serve ``frames[ticker]`` — the whole series for a 10y fetch, its last 20
    bars for anything shorter — and record every (tickers, period) request."""
    def fake(tickers, period="10y", chunk=50):
        calls.append((sorted(tickers), period))
        return {t: frames[t].iloc[0 if period == "10y" else -20:] for t in tickers}
    monkeypatch.setattr("stockanalysis.ingest.fetch_bulk_prices", fake)


def test_refresh_rebuilds_a_ticker_whose_history_was_readjusted(conn, monkeypatch):
    """A top-up that disagrees with the cache refetches that ticker's full
    period on the new basis instead of stitching two bases together."""
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist.iloc[:50])
    cache.upsert_bars(conn, "BBB", hist.iloc[:50])
    rebased = _readjusted(hist, ex_date_pos=55, factor=0.99)     # BBB went ex-div
    calls = []
    _fake_bulk(monkeypatch, {"AAA": hist, "BBB": rebased}, calls)

    res = cache.refresh(conn, ["AAA", "BBB"])

    assert calls == [(["AAA", "BBB"], "1mo"), (["BBB"], "10y")]
    assert res == {"fetched": [], "topped_up": ["AAA"], "rebuilt": ["BBB"],
                   "bars": 20 + 60}
    np.testing.assert_allclose(cache.load_bars(conn, "BBB")["Close"].to_numpy(),
                               rebased["Close"].to_numpy())
    np.testing.assert_allclose(cache.load_bars(conn, "AAA")["Close"].to_numpy(),
                               hist["Close"].to_numpy())


def test_refresh_fetches_whole_history_only_for_new_tickers(conn, monkeypatch):
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist.iloc[:50])
    calls = []
    _fake_bulk(monkeypatch, {"AAA": hist, "NEW": hist}, calls)

    res = cache.refresh(conn, ["AAA", "NEW"])

    assert calls == [(["NEW"], "10y"), (["AAA"], "1mo")]
    assert res["fetched"] == ["NEW"] and res["topped_up"] == ["AAA"]
    assert len(cache.load_bars(conn, "NEW")) == 60


def test_refresh_full_leaves_no_stale_basis_stub(conn, monkeypatch):
    """``full`` is the repair path; bars older than the new period window were
    fetched on the old basis and must go, not linger at the start of history."""
    hist = _frame(n=60)
    cache.upsert_bars(conn, "AAA", hist)
    calls = []
    _fake_bulk(monkeypatch, {"AAA": hist.iloc[5:] * 0.99}, calls)

    cache.refresh(conn, ["AAA"], full=True)

    assert calls == [(["AAA"], "10y")]
    back = cache.load_bars(conn, "AAA")
    assert back.index[0] == hist.index[5]
    np.testing.assert_allclose(back["Close"].to_numpy(),
                               hist["Close"].iloc[5:].to_numpy() * 0.99)
