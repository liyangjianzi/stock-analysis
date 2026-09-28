"""Tests for patterns.detect_patterns — fully offline.

Each path is drawn through hand-placed turning points (straight legs between
them), so the swing pivots, and the pattern they form, are known in advance.
"""
from __future__ import annotations

import pandas as pd
import pytest
from conftest import waypoint_ohlcv as _path

from stockanalysis.patterns import detect_patterns


def _find(patterns, name):
    return next((p for p in patterns if p["name"] == name), None)


def test_double_bottom_forming_below_its_neckline():
    p = _find(detect_patterns(_path([110, 90, 100, 90.2, 97])), "Double Bottom")
    assert p is not None
    assert p["bias"] == "bullish" and p["status"] == "forming"
    assert p["level"] == pytest.approx(100.3)          # the neckline: the middle high
    assert len(p["points"]) == 3


def test_double_bottom_confirms_on_a_close_above_the_neckline():
    p = _find(detect_patterns(_path([110, 90, 100, 90.2, 104])), "Double Bottom")
    assert p is not None and p["status"] == "confirmed"


def test_double_top_mirrors_the_bottom():
    p = _find(detect_patterns(_path([90, 110, 100, 109.8, 103])), "Double Top")
    assert p is not None
    assert p["bias"] == "bearish" and p["status"] == "forming"
    assert p["level"] == pytest.approx(99.7)


def test_unequal_lows_are_not_a_double_bottom():
    assert _find(detect_patterns(_path([110, 90, 100, 95, 97])), "Double Bottom") is None


def test_inverse_head_and_shoulders():
    p = _find(detect_patterns(_path([110, 95, 102, 88, 102.5, 95.2, 104])),
              "Inverse Head & Shoulders")
    assert p is not None
    assert p["bias"] == "bullish" and p["status"] == "confirmed"
    assert len(p["points"]) == 5


def test_head_and_shoulders():
    p = _find(detect_patterns(_path([90, 105, 98, 112, 97.5, 104.8, 96])), "Head & Shoulders")
    assert p is not None
    assert p["bias"] == "bearish" and p["status"] == "confirmed"


def test_ascending_triangle():
    p = _find(detect_patterns(_path([90, 100, 93, 100.1, 96, 99])), "Ascending Triangle")
    assert p is not None
    assert p["bias"] == "bullish" and p["status"] == "forming"
    assert p["level"] == pytest.approx(100.4)          # the flat top


def test_descending_triangle():
    p = _find(detect_patterns(_path([110, 100, 107, 99.9, 104, 101])), "Descending Triangle")
    assert p is not None and p["bias"] == "bearish"


def test_bull_flag():
    p = _find(detect_patterns(_path([100, 100, 120, 116], bars_per_leg=8)), "Bull Flag")
    assert p is not None
    assert p["bias"] == "bullish" and p["status"] == "forming"
    assert p["level"] == pytest.approx(120.3)          # the top of the pole


def test_a_deep_pullback_is_not_a_flag():
    """Giving back more than half the pole is a reversal, not a pause."""
    assert _find(detect_patterns(_path([100, 100, 120, 105], bars_per_leg=8)),
                 "Bull Flag") is None


def test_a_straight_trend_has_no_patterns():
    assert detect_patterns(_path([100, 160], bars_per_leg=120)) == []


def test_points_are_dated_pivots_in_order():
    p = _find(detect_patterns(_path([110, 90, 100, 90.2, 97])), "Double Bottom")
    dates = [d for d, _ in p["points"]]
    assert dates == sorted(dates)
    assert all(isinstance(d, pd.Timestamp) for d in dates)


@pytest.mark.parametrize("df", [
    None,
    pd.DataFrame(),
    pd.DataFrame({"High": [1.0, 2.0], "Low": [0.5, 1.5], "Close": [1.0, 2.0]}),
], ids=["none", "empty", "no-atr"])
def test_degenerate_input_yields_nothing(df):
    assert detect_patterns(df) == []
