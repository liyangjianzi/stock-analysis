"""Tests for ingest._safe and ingest.fetch_fundamentals.

No network: fetch_fundamentals is fed a plain ``info`` dict (the same shape
yfinance returns), so the unit-normalization logic is tested in isolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalysis.ingest import _safe, earnings_surprises, fetch_fundamentals


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


def test_dividend_yield_is_always_a_percent():
    # Yahoo serves dividendYield in percent on both sides of 1%: AAPL's 0.32%
    # arrives as 0.32 and KO's 2.41% as 2.41. A ">1 means percent" guess read
    # every sub-1% payer as a 32%-style yield that cleared the 1.5% screen.
    assert fetch_fundamentals("AAPL", {"dividendYield": 0.32})["Div_Yield"] == pytest.approx(0.0032)
    assert fetch_fundamentals("KO", {"dividendYield": 2.41})["Div_Yield"] == pytest.approx(0.0241)


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


# --- Next earnings date ---------------------------------------------------------

# 2100-01-01 16:00 New York (21:00 UTC) — after the close, like most reports.
_FUTURE_TS = 4102520400
_PAST_TS = 946684800                                  # 2000-01-01


def test_next_earnings_is_the_upcoming_date_in_new_york_time():
    out = fetch_fundamentals("AAPL", {"earningsTimestampStart": _FUTURE_TS})
    assert out["Next_Earnings"] == "2100-01-01"


def test_next_earnings_ignores_a_date_already_past():
    """Yahoo can lag a report by days — a stale date is not the next one."""
    assert fetch_fundamentals("AAPL", {"earningsTimestampStart": _PAST_TS})["Next_Earnings"] is None


def test_next_earnings_missing_is_none():
    out = fetch_fundamentals("AAPL", {})
    assert out["Next_Earnings"] is None
    assert out["Earnings_Est"] is None


def test_earnings_estimate_flag_passes_through():
    info = {"earningsTimestampStart": _FUTURE_TS, "isEarningsDateEstimate": True}
    assert fetch_fundamentals("AAPL", info)["Earnings_Est"] is True


# --- Earnings surprise history (the shape yfinance's get_earnings_dates returns) --

def _earnings_frame():
    idx = pd.DatetimeIndex(["2026-10-29 16:00", "2026-07-30 16:00", "2026-04-30 16:00",
                            "2026-01-29 16:00", "2025-10-30 16:00", "2025-07-31 16:00"],
                           tz="America/New_York", name="Earnings Date")
    return pd.DataFrame({"EPS Estimate": [1.98, 1.89, 1.94, 2.35, 1.77, 1.43],
                         "Reported EPS": [np.nan, 2.02, 2.01, 2.40, 1.85, 1.57],
                         "Surprise(%)": [np.nan, 6.74, 3.46, 2.13, 4.52, 9.79]}, index=idx)


def test_earnings_surprises_are_the_latest_reported_quarters_newest_first():
    out = earnings_surprises(_earnings_frame(), quarters=4)
    assert [q["date"] for q in out] == ["2026-07-30", "2026-04-30", "2026-01-29", "2025-10-30"]
    assert out[0]["eps_estimate"] == pytest.approx(1.89)
    assert out[0]["eps_reported"] == pytest.approx(2.02)


def test_earnings_surprise_is_normalised_from_percent_to_a_fraction():
    """Yahoo's Surprise(%) is a percent (6.74 = 6.74%); the package's growth
    fields are fractions, so this one is too."""
    assert earnings_surprises(_earnings_frame())[0]["surprise"] == pytest.approx(0.0674)


@pytest.mark.parametrize("frame", [None, pd.DataFrame()], ids=["none", "empty"])
def test_earnings_surprises_of_nothing_is_an_empty_list(frame):
    assert earnings_surprises(frame) == []
