"""Tests for chart layout details that are easy to regress (offline, no network).

The figures themselves are visual, but the right-hand margin is not: S/R labels
are drawn *into* that margin, so a margin narrower than the text silently clips
them ("RES 950.12" renders as "RES 9").
"""
from __future__ import annotations

from conftest import wandering_ohlcv as _wandering_ohlcv

from stockanalysis.charts import _right_margin, build_technical_dashboard
from stockanalysis.indicators import add_indicators

SR_FONT = 9   # annotation_font size used for the S/R labels


def _dashboard(scale: float):
    df = _wandering_ohlcv(scale)
    fig = build_technical_dashboard("T", {"T": add_indicators(df)})
    labels = [a.text for a in fig.layout.annotations if a.text.startswith(("SUP ", "RES "))]
    return fig, labels


def test_right_margin_grows_with_label_length():
    assert _right_margin(["RES 95.12"], SR_FONT) < _right_margin(["RES 12345.67"], SR_FONT)


def test_right_margin_grows_with_font_size():
    assert _right_margin(["Moderate/Elevated"], 12) > _right_margin(["Moderate/Elevated"], 9)


def test_right_margin_never_narrower_than_base():
    """No levels (or very short labels) -> keep the plain default margin."""
    assert _right_margin([], SR_FONT, base=30) == 30
    assert _right_margin(["X"], SR_FONT, base=30) == 30


def test_dashboard_margin_fits_its_support_resistance_labels():
    fig, labels = _dashboard(95)

    assert labels, "fixture should produce at least one S/R level"
    assert fig.layout.margin.r >= _right_margin(labels, SR_FONT)


def test_dashboard_margin_scales_with_price_magnitude():
    """A 4-digit price yields a wider label than a 2-digit one, so it needs more room."""
    cheap_fig, cheap_labels = _dashboard(95)
    pricey_fig, pricey_labels = _dashboard(9500)

    assert len(max(pricey_labels, key=len)) > len(max(cheap_labels, key=len))
    assert pricey_fig.layout.margin.r > cheap_fig.layout.margin.r
