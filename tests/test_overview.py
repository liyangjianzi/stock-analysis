"""Tests for overview.py's macro panel — pure stats over synthetic frames, and
the fetch paths with the network monkeypatched out."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalysis import config, overview
from stockanalysis.overview import fetch_fred, fred_summary, macro_stats


def _daily(values, end="2026-09-25") -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=len(values))
    return pd.DataFrame({"Close": np.asarray(values, dtype=float)}, index=idx)


def _path(last: float, week_ago: float, month_ago: float, n: int = 30) -> list[float]:
    """A path whose bar 5 back is ``week_ago`` and bar 21 back is ``month_ago``."""
    vals = [month_ago] * n
    vals[-6:-1] = [week_ago] * 5
    vals[-1] = last
    return vals


# --- macro_stats ----------------------------------------------------------------

def test_yield_changes_are_in_basis_points_and_levels_are_not_rescaled():
    """Yahoo quotes ^TNX in percent (5.18 = 5.18%) — the same unit trap as
    dividendYield, so pin that nothing multiplies or divides it."""
    stats = macro_stats({"10Y Treasury": _daily(_path(5.18, 5.00, 4.90))})
    row = stats["rates"][0]
    assert row["Name"] == "10Y Treasury"
    assert row["Last"] == pytest.approx(5.18)
    assert row["1W"] == pytest.approx(18.0)            # +18 bps
    assert row["1M"] == pytest.approx(28.0)
    assert row["Unit"] == "bps"


def test_market_changes_are_in_percent():
    stats = macro_stats({"WTI Crude": _daily(_path(110.0, 100.0, 88.0))})
    row = stats["rates"][0]
    assert row["1W"] == pytest.approx(10.0)            # +10%
    assert row["1M"] == pytest.approx(25.0)
    assert row["Unit"] == "%"


def test_curve_slope_is_ten_year_minus_three_month_in_bps():
    stats = macro_stats({"10Y Treasury": _daily([5.18] * 30),
                         "3M T-Bill": _daily([4.07] * 30)})
    assert stats["curve"]["slope_bps"] == pytest.approx(111.0)
    assert stats["curve"]["inverted"] is False


def test_an_inverted_curve_is_labelled():
    stats = macro_stats({"10Y Treasury": _daily([3.90] * 30),
                         "3M T-Bill": _daily([5.30] * 30)})
    assert stats["curve"]["inverted"] is True


def test_short_history_leaves_the_month_change_blank():
    row = macro_stats({"WTI Crude": _daily([80.0] * 10)})["rates"][0]
    assert np.isfinite(row["1W"]) and np.isnan(row["1M"])


def test_macro_stats_of_nothing():
    assert macro_stats({}) == {"rates": [], "curve": None}


# --- fred_summary ---------------------------------------------------------------

def _monthly(values, end="2026-08-01") -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float),
                     index=pd.date_range(end=end, periods=len(values), freq="MS"))


def test_cpi_is_reported_year_over_year():
    cpi = _monthly(np.linspace(100.0, 103.3, 14))   # 13 steps of 0.2538
    row = fred_summary({"CPI": cpi})[0]
    assert row["Indicator"] == "CPI"
    assert row["Value"] == pytest.approx((cpi.iloc[-1] / cpi.iloc[-13] - 1) * 100)
    assert row["Unit"] == "% y/y"
    assert row["As of"] == "2026-08-01"


def test_payrolls_are_the_monthly_change_in_thousands():
    row = fred_summary({"Payrolls": _monthly([159_000, 159_100, 159_175])})[0]
    assert row["Value"] == pytest.approx(75.0)
    assert row["Change"] == pytest.approx(-25.0)       # 75k vs 100k the month before
    assert row["Unit"] == "k"


def test_levels_carry_their_three_month_change():
    row = fred_summary({"Unemployment": _monthly([3.8, 3.9, 4.0, 4.1])})[0]
    assert row["Value"] == pytest.approx(4.1)
    assert row["Change"] == pytest.approx(0.3)


def test_a_series_too_short_to_transform_is_skipped():
    assert fred_summary({"CPI": _monthly([100.0, 101.0])}) == []


def test_fred_summary_of_nothing():
    assert fred_summary({}) == []


# --- fetch paths: never raise ------------------------------------------------------

def test_fetch_fred_skips_a_series_that_fails(monkeypatch):
    def fake_read(series_id):
        if series_id == "CPIAUCSL":
            raise OSError("offline")
        return _monthly([4.0, 4.1])
    monkeypatch.setattr(overview, "_read_fred_series", fake_read)
    out = fetch_fred({"CPI": "CPIAUCSL", "Unemployment": "UNRATE"})
    assert list(out) == ["Unemployment"]


def test_daily_overview_carries_the_macro_panel(monkeypatch):
    monkeypatch.setattr(overview, "fetch_index_data", lambda: {})
    monkeypatch.setattr(overview, "scan_candidates", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(overview, "recent_headlines", lambda tickers: [])
    monkeypatch.setattr(overview, "fetch_macro_data",
                        lambda: {"10Y Treasury": _daily([5.18] * 30),
                                 "3M T-Bill": _daily([4.07] * 30)})
    monkeypatch.setattr(overview, "fetch_fred",
                        lambda: {"Unemployment": _monthly([3.8, 3.9, 4.0, 4.1])})
    macro = overview.daily_overview(watchlist={})["macro"]
    assert [r["Name"] for r in macro["rates"]] == ["10Y Treasury", "3M T-Bill"]
    assert macro["curve"]["inverted"] is False
    assert macro["economy"][0]["Indicator"] == "Unemployment"


def test_macro_config_names_the_curve_legs():
    """macro_stats computes the slope from these two names — keep them in config."""
    assert {"10Y Treasury", "3M T-Bill"} <= set(config.MACRO_YIELDS)
