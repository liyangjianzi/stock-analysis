"""Tests for portfolio.py — household risk from holdings, fully offline.

The portfolio is invented (the repo is public): three stocks in two currencies,
cash in both, and one unpriced holding, with synthetic price paths.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalysis import portfolio
from stockanalysis.holdings import FRAME_COLUMNS


def _h(rows):
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


@pytest.fixture
def holdings():
    return _h([
        # account, ticker, yahoo, kind, shares, price, cost, currency, value, value_cad
        ["A USD", "AAA", "AAA", "stock", 10, 100.0, 50.0, "USD", 1000.0, 1400.0],
        ["B USD", "AAA", "AAA", "stock", 5, 100.0, 60.0, "USD", 500.0, 700.0],
        ["B CAD", "BBB", "BBB.TO", "stock", 100, 30.0, 20.0, "CAD", 3000.0, 3000.0],
        ["A USD", "CCC", "CCC", "stock", 20, 25.0, 30.0, "USD", 500.0, 700.0],
        ["A USD", "MMF", "MMF.TO", "stock", 10, 50.0, 50.0, "USD", 500.0, 700.0],
        ["A USD", "Cash_USD", None, "cash", np.nan, np.nan, np.nan, "USD", 1000.0, 1400.0],
        ["B CAD", "Cash_CAD", None, "cash", np.nan, np.nan, np.nan, "CAD", 1100.0, 1100.0],
        ["C", "POLICY", None, "other", np.nan, np.nan, np.nan, "CAD", 1000.0, 1000.0],
    ])  # total 10,000 CAD


@pytest.fixture
def info():
    return {"AAA": {"sector": "Technology", "name": "Aaa Inc."},
            "BBB.TO": {"sector": "Financial Services", "name": "Bbb Corp."},
            "CCC": {"sector": "Technology", "name": "Ccc Inc."},
            "MMF.TO": {"sector": None, "name": "Some U.S. Cash Management ETF",
                       "cash_equivalent": True}}


# --- exposure ---------------------------------------------------------------------

def test_exposure_combines_a_holding_across_accounts(holdings, info):
    ex = portfolio.exposure(holdings, info)
    rows = ex["holdings"].set_index("name")
    assert ex["total_cad"] == pytest.approx(10_000.0)
    assert rows.loc["AAA", "value_cad"] == pytest.approx(2100.0)
    assert rows.loc["AAA", "weight"] == pytest.approx(0.21)
    assert rows.loc["AAA", "accounts"] == 2


def test_a_cash_management_fund_counts_as_cash(holdings, info):
    ex = portfolio.exposure(holdings, info)
    assert ex["cash_weight"] == pytest.approx((1400 + 1100 + 700) / 10_000)
    assert "MMF.TO" not in set(ex["holdings"]["name"])


def test_sectors_currencies_and_accounts(holdings, info):
    ex = portfolio.exposure(holdings, info)
    sectors = ex["sectors"].set_index("sector")["weight"]
    assert sectors["Technology"] == pytest.approx(0.28)
    assert sectors["Cash"] == pytest.approx(0.32)
    assert sectors["Other (unpriced)"] == pytest.approx(0.10)
    ccy = ex["currencies"].set_index("currency")["weight"]
    assert ccy["USD"] == pytest.approx(0.49)
    accounts = ex["accounts"].set_index("account")
    assert accounts.loc["B CAD", "cash_cad"] == pytest.approx(1100.0)


def test_concentration_flags(holdings, info):
    ex = portfolio.exposure(holdings, info, max_position=0.25, max_sector=0.30)
    assert ex["top5_weight"] == pytest.approx(0.30 + 0.21 + 0.07 + 0.10)
    assert any("BBB.TO" in f and "30.0%" in f for f in ex["flags"])
    assert not any(f.startswith("AAA") for f in ex["flags"])       # 21% < 25%
    assert not any("Cash" in f for f in ex["flags"])                # cash is never "concentrated"


# --- market risk ------------------------------------------------------------------

def _returns(n=600, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    market = rng.normal(0.0005, 0.01, n)
    return pd.DataFrame({"AAA": 1.5 * market + rng.normal(0, 0.005, n),
                         "BBB.TO": 0.5 * market + rng.normal(0, 0.005, n)}, index=idx), \
        pd.DataFrame({"S&P 500": market}, index=idx)


def test_market_risk_scales_with_the_invested_share():
    rets, bench = _returns()
    full = portfolio.market_risk(pd.Series({"AAA": 1.0}), rets, bench, total_cad=100_000)
    half = portfolio.market_risk(pd.Series({"AAA": 0.5}), rets, bench, total_cad=100_000)
    assert half["vol_ann"] == pytest.approx(full["vol_ann"] / 2, rel=1e-9)
    assert full["beta"]["S&P 500"] == pytest.approx(1.5, abs=0.1)
    assert full["var_1d"] > 0 and full["cvar_1d"] >= full["var_1d"]
    assert full["var_21d"] > full["var_1d"]
    assert full["var_1d_cad"] == pytest.approx(full["var_1d"] * 100_000)


def test_risk_shares_sum_to_one_and_favour_the_volatile_name():
    rets, bench = _returns()
    mr = portfolio.market_risk(pd.Series({"AAA": 0.3, "BBB.TO": 0.3}), rets, bench, total_cad=1.0)
    shares = mr["contributions"].set_index("name")["risk_share"]
    assert shares.sum() == pytest.approx(1.0)
    assert shares["AAA"] > shares["BBB.TO"]                         # same weight, 3x the beta


def test_short_history_is_reported_not_hidden():
    rets, bench = _returns()
    rets.loc[rets.index[:400], "BBB.TO"] = np.nan
    mr = portfolio.market_risk(pd.Series({"AAA": 0.3, "BBB.TO": 0.3}), rets, bench, total_cad=1.0)
    assert mr["short_history"] == ["BBB.TO"]


# --- stress ----------------------------------------------------------------------------

def _prices():
    idx = pd.bdate_range("2020-01-01", "2020-06-30")
    fall = np.where(idx < pd.Timestamp("2020-02-20"), 100.0,
                    np.where(idx <= pd.Timestamp("2020-03-23"), 60.0, 80.0))
    return pd.DataFrame({"AAA": fall, "BBB.TO": fall / 2 + 50,
                         "NEW": np.where(idx >= pd.Timestamp("2020-05-01"), 10.0, np.nan),
                         "PROXY": fall * 0.9}, index=idx)


def test_stress_replays_current_weights_through_a_window():
    res = portfolio.stress(pd.Series({"AAA": 0.5, "BBB.TO": 0.2}), _prices(),
                           {"Crash": ("2020-02-19", "2020-03-23")}, total_cad=100_000)[0]
    # AAA -40%, BBB -20% -> 0.5*-0.4 + 0.2*-0.2 = -0.24
    assert res["return"] == pytest.approx(-0.24)
    assert res["loss_cad"] == pytest.approx(-24_000)
    assert res["covered_weight"] == pytest.approx(0.7)


def test_stress_uses_a_proxy_for_a_name_without_history_else_lists_it():
    w = pd.Series({"AAA": 0.5, "NEW": 0.2})
    win = {"Crash": ("2020-02-19", "2020-03-23")}
    missing = portfolio.stress(w, _prices(), win, total_cad=1.0)[0]
    assert missing["missing"] == ["NEW"] and missing["covered_weight"] == pytest.approx(0.5)
    proxied = portfolio.stress(w, _prices(), win, total_cad=1.0, proxies={"NEW": "PROXY"})[0]
    assert proxied["proxied"] == {"NEW": "PROXY"}
    assert proxied["return"] == pytest.approx(0.5 * -0.4 + 0.2 * -0.4)


# --- account drawdown -----------------------------------------------------------------

def test_contributions_do_not_hide_a_loss():
    """100 -> 90 (a 10% loss), then +50 of new money lifts the balance to 140:
    the raw balance shows a new high, the time-weighted index doesn't."""
    idx = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"])
    history = pd.Series([100.0, 90.0, 140.0], index=idx)
    contributions = pd.Series([50.0], index=pd.to_datetime(["2025-01-03"]))
    dd = portfolio.account_drawdown(history, contributions)
    assert dd["max_drawdown"] == pytest.approx(-0.10)
    assert dd["current_drawdown"] == pytest.approx(-0.10)    # still 10% under water...
    assert dd["raw_current_drawdown"] == pytest.approx(0.0)  # ...though the balance is a new high
    assert dd["contributions_cad"] == pytest.approx(50.0)


def test_too_little_history_is_no_drawdown_at_all():
    """An empty result, not a dict of NaN the report would trip over."""
    assert portfolio.account_drawdown(pd.Series([100.0], index=pd.to_datetime(["2025-01-01"]))) == {}


def test_contributions_before_the_history_are_ignored():
    idx = pd.to_datetime(["2025-01-01", "2025-01-02"])
    dd = portfolio.account_drawdown(pd.Series([100.0, 110.0], index=idx),
                                    pd.Series([999.0], index=pd.to_datetime(["2024-06-01"])))
    assert dd["twr"] == pytest.approx(0.10)
    assert dd["contributions_cad"] == pytest.approx(0.0)


# --- stop-based risk and funding ----------------------------------------------------------

def test_stop_risk_is_the_loss_to_each_plans_stop(holdings, info, monkeypatch):
    monkeypatch.setattr(portfolio, "build_trade_plan",
                        lambda df: {"stop": 90.0 if df.name == "AAA" else np.nan,
                                    "stop_basis": "structure"})
    tech = {"AAA": pd.DataFrame({"Close": [100.0]}), "CCC": pd.DataFrame({"Close": [25.0]})}
    for k, v in tech.items():
        v.name = k
    rows = portfolio.exposure(holdings, info)["holdings"]
    sr = portfolio.stop_risk(rows[rows["kind"] == "stock"], tech, usdcad=1.4, total_cad=10_000)
    row = sr["rows"].set_index("name").loc["AAA"]
    assert row["loss_cad"] == pytest.approx(15 * (100 - 90) * 1.4)   # 15 shares, USD->CAD
    assert sr["heat"] == pytest.approx(210.0 / 10_000)
    assert "CCC" in sr["no_stop"]


def test_funding_lists_the_accounts_that_can_pay_for_each_buy(holdings):
    matrix = pd.DataFrame({"Ticker": ["ZZZ", "YYY.TO", "XUS-U.TO"],
                           "Final Action Signal": ["Buy", "Buy", "Buy"],
                           "Shares": [10, 10, 1], "Entry": [50.0, 200.0, 50.0]})
    fund = portfolio.funding(holdings, matrix, usdcad=1.4)
    buys = fund["buys"].set_index("Ticker")
    assert buys.loc["ZZZ", "notional"] == pytest.approx(500.0)
    assert buys.loc["ZZZ", "currency"] == "USD"
    assert buys.loc["ZZZ", "accounts"] == ["A USD"]                # has 1,000 USD cash
    assert buys.loc["YYY.TO", "accounts"] == []                     # needs 2,000 CAD; best is 1,100
    assert buys.loc["XUS-U.TO", "currency"] == "USD"                # USD-traded TSX units
    assert any("YYY.TO" in f for f in fund["flags"])


# --- describing securities, CAD conversion, assembly ---------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ({"quoteType": "EQUITY", "sector": "Technology", "longName": "Aaa Inc."},
     {"sector": "Technology", "cash_equivalent": False, "proxy": None}),
    ({"quoteType": "ETF", "longName": "Some U.S. Cash Management ETF", "currency": "USD"},
     {"sector": "Cash", "cash_equivalent": True, "proxy": None}),
    ({"quoteType": "ETF", "longName": "A Money Market Fund", "currency": "CAD"},
     {"sector": "Cash", "cash_equivalent": True, "proxy": None}),
    ({"quoteType": "ETF", "longName": "Some Bitcoin ETF", "currency": "CAD"},
     {"sector": "Crypto", "cash_equivalent": False, "proxy": "BTC-CAD"}),
    ({"quoteType": "ETF", "longName": "Tech-Software ETF", "category": "Technology"},
     {"sector": "Technology", "cash_equivalent": False, "proxy": None}),
    ({"quoteType": "ETF", "longName": "NASDAQ 100 Index ETF"},
     {"sector": "Index fund", "cash_equivalent": False, "proxy": None}),
    ({}, {"sector": "Unknown", "cash_equivalent": False, "proxy": None}),
])
def test_describe_security(raw, expected):
    d = portfolio.describe(raw)
    assert {k: d[k] for k in expected} == expected
    assert d["currency"] == (raw.get("currency") or "USD").upper()


def test_to_cad_converts_usd_series_and_leaves_cad_alone():
    idx = pd.bdate_range("2024-01-01", periods=3)
    closes = pd.DataFrame({"U1": [10.0, 11.0, 12.0], "C1.TO": [5.0, 5.0, 5.0]}, index=idx)
    fx = pd.Series([1.3, 1.4, 1.5], index=idx)
    out = portfolio.to_cad(closes, {"U1": "USD", "C1.TO": "CAD"}, fx)
    assert list(out["U1"]) == pytest.approx([13.0, 15.4, 18.0])
    assert list(out["C1.TO"]) == [5.0, 5.0, 5.0]


def test_build_risk_assembles_every_section(holdings, info):
    idx = pd.bdate_range("2017-01-02", "2026-09-25")
    rng = np.random.default_rng(0)
    walk = lambda: pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx)
    closes = pd.DataFrame({"AAA": walk(), "BBB.TO": walk(), "CCC": walk()})
    market = {"info": info, "closes_cad": closes, "bench_cad": pd.DataFrame({"S&P 500": walk()}),
              "tech": {}, "usdcad": 1.4, "proxies": {}}
    history = pd.Series([100.0, 95.0, 99.0], index=pd.to_datetime(["2026-09-23", "2026-09-24", "2026-09-25"]))
    risk = portfolio.build_risk(holdings, market, history=history,
                                contributions=pd.Series(dtype=float))
    assert set(risk) >= {"exposure", "market", "stress", "drawdown", "stops", "funding"}
    assert np.isfinite(risk["market"]["vol_ann"])
    assert len(risk["stress"]) == len(portfolio.config.STRESS_WINDOWS)
    assert risk["drawdown"]["max_drawdown"] == pytest.approx(-0.05)
    assert risk["funding"]["buys"].empty                       # no signal matrix passed


def test_usd_cash_moves_with_the_exchange_rate_in_stress_and_risk(holdings, info):
    """USD cash is flat in USD but not in CAD: with the USD/CAD series in
    ``market["fx"]`` it is weighted like any holding."""
    idx = pd.bdate_range("2017-01-02", "2026-09-25")
    flat = pd.Series(100.0, index=idx)
    fx = pd.Series(np.where(idx < pd.Timestamp("2020-02-20"), 1.30, 1.43), index=idx)
    closes = pd.DataFrame({"AAA": flat, "BBB.TO": flat, "CCC": flat})
    market = {"info": info, "closes_cad": closes, "bench_cad": pd.DataFrame({"S&P 500": flat}),
              "tech": {}, "usdcad": 1.43, "proxies": {}, "fx": {"USD": fx}}
    risk = portfolio.build_risk(holdings, market)
    covid = next(s for s in risk["stress"] if s["scenario"] == "COVID crash")
    usd_cash_weight = (1400 + 700) / 10_000                 # Cash_USD + the money-market fund
    assert covid["return"] == pytest.approx(usd_cash_weight * (1.43 / 1.30 - 1))
