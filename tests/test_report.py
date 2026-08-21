"""Tests for report.py: the combined-report renderer + writer.

Pure-logic + tmp-dir I/O, fully offline (no network / yfinance).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from conftest import wandering_ohlcv as _wandering_ohlcv

from stockanalysis import report
from stockanalysis.indicators import add_indicators


def _screened_df(tickers):
    df = pd.DataFrame({
        "Sector": ["Technology"] * len(tickers),
        "PE": [20.0] * len(tickers),
        "EPS_Growth": [0.12] * len(tickers),
        "Rev_Growth": [0.09] * len(tickers),
        "Debt_Equity": [0.4] * len(tickers),
        "Div_Yield": [0.02] * len(tickers),
        "FCF": [1_000_000.0] * len(tickers),
        "Fundamental_Score": list(range(len(tickers), 0, -1)),
    }, index=list(tickers))
    df.index.name = "Ticker"
    return df


def _signal_matrix(tickers, actions=None, postures=None):
    actions = actions or ["Buy"] * len(tickers)
    postures = postures or ["Bullish"] * len(tickers)
    return pd.DataFrame({
        "Ticker": tickers, "Sector": ["Technology"] * len(tickers),
        "Fundamental Score": [5] * len(tickers), "Technical Posture": postures,
        "Tech Score": [6] * len(tickers), "Composite": [0.8] * len(tickers),
        "Final Action Signal": actions,
    })


def _profile(ticker, report_text="ascii report body", fundamentals=None):
    return {
        "ticker": ticker, "name": f"{ticker} Inc.", "sector": "Technology",
        "industry": "Software", "country": "US", "fundamental_score": 5,
        "scores": {"management": 2, "moat": 3, "long_term": 3},
        "fundamentals": fundamentals or {
            "pe": 20.0, "eps_growth": 0.12, "rev_growth": 0.09,
            "fcf": 1_000_000.0, "debt_equity": 0.4,
        },
        "raw": {
            "grossMargins": 0.45, "operatingMargins": 0.25, "profitMargins": 0.15,
            "returnOnEquity": 0.20, "returnOnAssets": 0.10,
            "earningsGrowth": 0.11, "revenueGrowth": 0.08,
            "currentRatio": 1.5, "quickRatio": 1.2, "totalCash": 5_000_000.0,
            "priceToBook": 3.0, "priceToSalesTrailing12Months": 4.0,
            "pegRatio": 1.1, "enterpriseToEbitda": 12.0,
            "heldPercentInsiders": 0.10, "heldPercentInstitutions": 0.60,
            "shortPercentOfFloat": 0.02,
        },
        "report": report_text,
    }


def _overview_data(vix=15.0):
    index_data = {"S&P 500": {"full": _wandering_ohlcv(4000), "chart": _wandering_ohlcv(4000)}}
    return {
        "index_data": index_data,
        "indices": [{"Index": "S&P 500", "Last": 4000.0, "Day %": 0.5,
                    "Week %": 1.2, "YTD %": np.nan, "RSI": 55.0, "Trend": "Above EMA50"}],
        "vix": {"value": vix, "label": "LOW"} if vix is not None else None,
        "headlines": [{"published": "01-01 09:00", "title": "Market rallies",
                       "publisher": "Reuters"}],
        "candidates": pd.DataFrame(),
        "action_plan": {"breakdown": {"Buy": 1}, "top_buys": [{"Ticker": "AAA", "Composite": 0.8}]},
    }


def test_build_full_report_includes_all_five_sections():
    tickers = ["AAA", "BBB"]
    tech = {t: add_indicators(_wandering_ohlcv(100)) for t in tickers}
    profiles = [_profile(t) for t in tickers]
    out = report.build_full_report(
        _screened_df(tickers), _signal_matrix(tickers), tech, profiles,
        _overview_data(), selected=tickers, generated_at="2026-08-20 12:00",
    )

    assert "<html" in out and "</html>" in out
    for anchor in ("screener", "signals", "dashboards", "profiles", "overview"):
        assert f'id="{anchor}"' in out
    assert "AAA" in out and "BBB" in out


def test_build_full_report_screener_and_signals_are_not_capped_by_selected():
    tickers = ["AAA", "BBB", "CCC"]
    tech = {t: add_indicators(_wandering_ohlcv(100)) for t in tickers}
    selected = tickers[:1]
    profiles = [_profile(t) for t in selected]
    out = report.build_full_report(
        _screened_df(tickers), _signal_matrix(tickers), tech, profiles,
        _overview_data(), selected=selected, generated_at="now",
    )

    for t in tickers:
        assert t in out  # every screened ticker shows up in the tables
    assert 'id="plt-BBB"' not in out  # but only the selected ticker gets a dashboard
    assert 'id="plt-CCC"' not in out


def test_build_full_report_escapes_html_in_values():
    tickers = ["AAA"]
    screened = _screened_df(tickers)
    screened["Sector"] = ["<script>alert(1)</script>"]
    out = report.build_full_report(
        screened, _signal_matrix(tickers), {}, [], _overview_data(),
        selected=[], generated_at="now",
    )

    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_build_full_report_badge_colors_present():
    tickers = ["AAA", "BBB"]
    out = report.build_full_report(
        _screened_df(tickers),
        _signal_matrix(tickers, actions=["Buy", "Watch"], postures=["Bullish", "Bearish"]),
        {}, [], _overview_data(), selected=[], generated_at="now",
    )

    assert "#B7E1CD" in out  # Buy / Bullish
    assert "#D9D9D9" in out  # Watch
    assert "#F4C7C3" in out  # Bearish


def test_build_full_report_empty_screened_df_and_signal_matrix():
    out = report.build_full_report(
        pd.DataFrame(), pd.DataFrame(), {}, [], _overview_data(),
        selected=[], generated_at="now",
    )

    assert "No fundamentals passed the screener." in out
    assert "No signals generated." in out
    assert "No tickers selected." in out
    assert "No profiles selected." in out


def test_build_full_report_no_tech_data_for_selected_ticker():
    out = report.build_full_report(
        _screened_df(["AAA"]), _signal_matrix(["AAA"]), {}, [_profile("AAA")],
        _overview_data(), selected=["AAA"], generated_at="now",
    )

    assert "No chart data for AAA." in out


def test_build_full_report_overview_vix_none():
    out = report.build_full_report(
        _screened_df(["AAA"]), _signal_matrix(["AAA"]), {}, [],
        _overview_data(vix=None), selected=[], generated_at="now",
    )

    assert "VIX: unavailable" in out


def test_build_full_report_empty_overview():
    out = report.build_full_report(
        _screened_df(["AAA"]), _signal_matrix(["AAA"]), {}, [],
        {"index_data": {}, "indices": [], "vix": None, "headlines": [],
         "candidates": pd.DataFrame(), "action_plan": {}},
        selected=[], generated_at="now",
    )

    assert "No index data available." in out
    assert "VIX: unavailable" in out
    assert "No index stats available." in out
    assert "No recent headlines." in out


def test_build_full_report_profile_card_uses_structured_fields_not_raw_report_text():
    sentinel = "ZZZ_SENTINEL_NOT_ELSEWHERE_ZZZ"
    profiles = [_profile("AAA", report_text=sentinel)]
    out = report.build_full_report(
        _screened_df(["AAA"]), _signal_matrix(["AAA"]), {}, profiles,
        _overview_data(), selected=["AAA"], generated_at="now",
    )

    assert sentinel not in out


def test_build_full_report_profile_card_renders_resolved_fundamentals():
    """report.py trusts profile_dict["fundamentals"] as-is (the resolved,
    screened_df-preferred values profile.build_profile already computed) —
    it doesn't re-derive anything from screened_df itself."""
    profiles = [_profile("AAA", fundamentals={
        "pe": 18.5, "eps_growth": 0.20, "rev_growth": 0.15,
        "fcf": 2_000_000.0, "debt_equity": 0.3,
    })]
    out = report.build_full_report(
        _screened_df(["AAA"]), _signal_matrix(["AAA"]), {}, profiles, _overview_data(),
        selected=["AAA"], generated_at="now",
    )

    assert "18.50" in out


def test_save_report_writes_utf8_and_creates_parent_dirs(tmp_path):
    html_doc = "<html><body>hello — ✓</body></html>"
    path = report.save_report(html_doc, tmp_path / "run" / "nested" / "report.html")

    assert isinstance(path, str)
    written = tmp_path / "run" / "nested" / "report.html"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == html_doc
