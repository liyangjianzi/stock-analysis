"""Tests for screener.screen_fundamentals — the 0-6 fundamental score."""
from __future__ import annotations

import pandas as pd

from stockanalysis.screener import screen_fundamentals

PASS_COLS = ["Pass_PE", "Pass_EPS", "Pass_Rev", "Pass_DE", "Pass_Div", "Pass_FCF"]


def test_all_pass_and_all_fail_scores(fundamentals_df):
    out = screen_fundamentals(fundamentals_df)
    assert out.loc["GOOD", "Fundamental_Score"] == 6
    assert out.loc["GOOD", PASS_COLS].all()
    assert out.loc["BAD", "Fundamental_Score"] == 0
    assert not out.loc["BAD", PASS_COLS].any()


def test_nan_row_fails_every_metric_without_raising(fundamentals_df):
    # The "NaN means fail, never crash" contract.
    out = screen_fundamentals(fundamentals_df)
    assert out.loc["NAN", "Fundamental_Score"] == 0
    assert not out.loc["NAN", PASS_COLS].any()
    # score column is a real int, not a float/object
    assert out["Fundamental_Score"].dtype.kind in "iu"


def test_threshold_boundaries_are_strict():
    # Defaults: PE < 25 (strict), EPS_Growth > 0.10 (strict), DE < 1.0, etc.
    df = pd.DataFrame(
        {"PE": [25.0], "EPS_Growth": [0.10], "Rev_Growth": [0.08],
         "Debt_Equity": [1.0], "Div_Yield": [0.015], "FCF": [0.0]},
        index=["EDGE"],
    )
    out = screen_fundamentals(df)
    # Every metric sits exactly on its threshold -> none pass (all comparisons strict).
    assert out.loc["EDGE", "Fundamental_Score"] == 0


def test_output_sorted_by_score_desc(fundamentals_df):
    out = screen_fundamentals(fundamentals_df)
    scores = out["Fundamental_Score"].tolist()
    assert scores == sorted(scores, reverse=True)
    assert out.index[0] == "GOOD"  # highest score ranked first


def test_empty_input_returns_empty_frame():
    assert screen_fundamentals(pd.DataFrame()).empty
    assert screen_fundamentals(None).empty


def test_does_not_mutate_input(fundamentals_df):
    before = fundamentals_df.copy()
    screen_fundamentals(fundamentals_df)
    pd.testing.assert_frame_equal(fundamentals_df, before)
