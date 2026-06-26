"""Tests for ingest._safe and ingest.fetch_fundamentals.

No network: fetch_fundamentals is fed a plain ``info`` dict (the same shape
yfinance returns), so the unit-normalization logic is tested in isolation.
"""
from __future__ import annotations

import numpy as np

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
