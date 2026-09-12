"""Tests for ingest._safe and ingest.fetch_fundamentals.

No network: fetch_fundamentals is fed a plain ``info`` dict (the same shape
yfinance returns), so the unit-normalization logic is tested in isolation.
"""
from __future__ import annotations

import types

import numpy as np
import pandas as pd

from stockanalysis import ingest
from stockanalysis.ingest import _safe, fetch_fundamentals


# --- _safe ---------------------------------------------------------------------

def test_safe_returns_float_for_valid_number():
    assert _safe({"x": "3.5"}, "x") == 3.5
    assert isinstance(_safe({"x": 2}, "x"), float)


def test_safe_returns_nan_for_missing_none_nonnumeric_and_inf():
    assert np.isnan(_safe({}, "missing"))
    assert np.isnan(_safe({"x": None}, "x"))
    assert np.isnan(_safe({"x": "abc"}, "x"))
    assert np.isnan(_safe({"x": float("inf")}, "x"))


# --- fetch_fundamentals normalization -----------------------------------------

def test_debt_to_equity_normalized_from_percent():
    info = {"debtToEquity": 85.3}
    out = fetch_fundamentals("AAPL", info)
    assert out["Debt_Equity"] == 85.3 / 100.0  # -> 0.853


def test_dividend_yield_percent_is_normalized_but_fraction_is_kept():
    # > 1 looks like a percent -> /100
    assert fetch_fundamentals("X", {"dividendYield": 1.6})["Div_Yield"] == 0.016
    # already fractional -> untouched
    assert fetch_fundamentals("Y", {"dividendYield": 0.016})["Div_Yield"] == 0.016


def test_growth_fields_pass_through_unchanged():
    info = {"earningsGrowth": 0.20, "revenueGrowth": 0.08}
    out = fetch_fundamentals("Z", info)
    assert out["EPS_Growth"] == 0.20
    assert out["Rev_Growth"] == 0.08


def test_missing_fields_are_nan():
    out = fetch_fundamentals("EMPTY", {})
    for key in ("Price", "PE", "EPS_Growth", "Rev_Growth", "Debt_Equity",
                "Div_Yield", "FCF"):
        assert np.isnan(out[key]), f"{key} should be NaN when missing"


def test_sector_lookup_precedence():
    # 1) watchlist wins
    out = fetch_fundamentals("AAPL", {"sector": "FromInfo"},
                             watchlist={"AAPL": "Technology"})
    assert out["Sector"] == "Technology"
    # 2) falls back to info['sector'] when ticker absent from watchlist
    out = fetch_fundamentals("AAPL", {"sector": "FromInfo"}, watchlist={})
    assert out["Sector"] == "FromInfo"
    # 3) finally 'Unknown'
    out = fetch_fundamentals("AAPL", {}, watchlist={})
    assert out["Sector"] == "Unknown"


def test_ticker_is_echoed():
    assert fetch_fundamentals("NVDA", {})["Ticker"] == "NVDA"


# --- price caching -------------------------------------------------------------

def _ohlcv(n=5):
    # Ends today: fetch_stock_data slices by period, and a stale-dated fixture
    # would correctly slice down to nothing.
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=n)
    close = pd.Series([100.0 + i for i in range(n)], index=idx)
    return pd.DataFrame(
        {"Open": close, "High": close * 1.01, "Low": close * 0.99,
         "Close": close, "Volume": 1_000_000},
        index=idx,
    )


def _stub_info(monkeypatch, info=None):
    """Stub yf.Ticker so only the .info half of fetch_stock_data is faked."""
    monkeypatch.setattr(ingest.yf, "Ticker",
                        lambda ticker: types.SimpleNamespace(info=info or {}))


def test_fetch_stock_data_without_cache_requests_the_full_period(monkeypatch, tmp_path):
    calls = []

    def fake_raw(ticker, period=None, start=None):
        calls.append((period, start))
        return _ohlcv()

    monkeypatch.setattr(ingest, "_raw_history", fake_raw)
    _stub_info(monkeypatch)

    hist, _ = ingest.fetch_stock_data("AAPL", period="3y",
                                      use_cache=False, cache_dir=tmp_path)

    assert calls == [("3y", None)]
    assert len(hist) == 5
    assert not list(tmp_path.iterdir())      # disk untouched when caching is off


def test_fetch_stock_data_with_cache_writes_then_reuses_the_file(monkeypatch, tmp_path):
    calls = []

    def fake_raw(ticker, period=None, start=None):
        calls.append((period, start))
        return _ohlcv()

    monkeypatch.setattr(ingest, "_raw_history", fake_raw)
    _stub_info(monkeypatch)

    ingest.fetch_stock_data("AAPL", period="3y", use_cache=True, cache_dir=tmp_path)
    ingest.fetch_stock_data("AAPL", period="3y", use_cache=True, cache_dir=tmp_path)

    assert calls[0] == ("max", None)         # cold miss pulls complete history
    assert calls[1][0] is None               # second run tops up the tail only
    assert calls[1][1] is not None


def test_fetch_stock_data_returns_none_pair_when_there_is_no_history(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "_raw_history", lambda t, period=None, start=None: None)
    _stub_info(monkeypatch)

    assert ingest.fetch_stock_data("AAPL", use_cache=True, cache_dir=tmp_path) == (None, None)
