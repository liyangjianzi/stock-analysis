"""Tests for profile.py — fully offline (fetch_profile is monkeypatched;
no network / yfinance)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockanalysis import profile as profile_mod
from stockanalysis.profile import build_profile, save_report

# Every field build_profile reads off fetch_profile's return dict.
_FAKE_RAW = {
    "shortName": "Acme Corp", "longBusinessSummary": "Widgets.",
    "sector": "Technology", "industry": "Software", "country": "US",
    "numberOfEmployees": 1000,
    "grossMargins": 0.5, "operatingMargins": 0.2, "profitMargins": 0.1,
    "returnOnAssets": 0.05, "returnOnEquity": 0.1,
    "earningsGrowth": 0.05, "revenueGrowth": 0.04,   # raw fallback values
    "currentRatio": 1.0, "quickRatio": 1.0, "totalCash": 1.0e6,
    "priceToBook": 2.0, "priceToSalesTrailing12Months": 3.0,
    "pegRatio": 1.0, "enterpriseToEbitda": 10.0,
    "heldPercentInsiders": 0.1, "heldPercentInstitutions": 0.5,
    "shortPercentOfFloat": 0.02,
}


def test_build_profile_fundamentals_prefer_screened_df_over_raw(monkeypatch):
    """The ``fundamentals`` field is what report.py's HTML profile card reads
    directly — it must carry the same screened_df-preferred values the ASCII
    report body uses, not the raw yfinance fallbacks."""
    monkeypatch.setattr(profile_mod, "fetch_profile", lambda ticker: _FAKE_RAW)
    screened_df = pd.DataFrame(
        {"Fundamental_Score": [5], "PE": [18.5], "EPS_Growth": [0.20],
         "Rev_Growth": [0.15], "Debt_Equity": [0.3], "FCF": [2.0e6]},
        index=["ACME"],
    )
    screened_df.index.name = "Ticker"

    result = build_profile("ACME", screened_df)

    assert result["fundamentals"] == {
        "pe": 18.5, "eps_growth": 0.20, "rev_growth": 0.15,
        "fcf": 2.0e6, "debt_equity": 0.3,
    }


def test_build_profile_fundamentals_fall_back_to_raw_without_screened_df(monkeypatch):
    monkeypatch.setattr(profile_mod, "fetch_profile", lambda ticker: _FAKE_RAW)

    result = build_profile("ACME")

    assert result["fundamentals"]["eps_growth"] == 0.05
    assert result["fundamentals"]["rev_growth"] == 0.04
    assert pd.isna(result["fundamentals"]["pe"])
    assert pd.isna(result["fundamentals"]["fcf"])
    assert pd.isna(result["fundamentals"]["debt_equity"])


# The real report is drawn with box characters, an em dash and a check mark —
# exactly the bytes that break under a non-UTF-8 default locale.
REPORT = "══════\n  ACME · Moat ✓  ·  P/E —\n══════\n"


def test_save_report_writes_utf8_verbatim(tmp_path):
    path = save_report({"report": REPORT}, tmp_path / "ACME_profile.txt")

    assert Path(path).read_text(encoding="utf-8") == REPORT
    assert Path(path).read_bytes().decode("utf-8") == REPORT   # not the platform default


def test_save_report_creates_parent_dirs(tmp_path):
    """Mirrors charts.save_html: the caller never has to mkdir first."""
    path = save_report({"report": REPORT}, tmp_path / "run" / "nested" / "ACME_profile.txt")

    assert Path(path).exists()


def test_save_report_returns_the_path_as_a_string(tmp_path):
    target = tmp_path / "ACME_profile.txt"
    path = save_report({"report": REPORT}, target)

    assert isinstance(path, str)
    assert Path(path) == target


def test_save_report_overwrites_an_existing_file(tmp_path):
    target = tmp_path / "ACME_profile.txt"
    save_report({"report": "stale"}, target)
    save_report({"report": REPORT}, target)

    assert target.read_text(encoding="utf-8") == REPORT
