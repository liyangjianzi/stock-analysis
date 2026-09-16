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
