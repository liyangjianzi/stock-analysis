from __future__ import annotations

import numpy as np
import pandas as pd

from stockanalysis.backtest import posture_timeline


def test_posture_timeline_is_point_in_time(uptrend_ohlcv):
    """Truncating future bars must not change any past label/score.

    A naive impl that runs add_indicators once on the full series fails here,
    because the envelope percentiles (ENV_UP/DOWN) would peek at the future.
    """
    hist = uptrend_ohlcv
    cut = 180
    full = posture_timeline(hist, min_bars=60)
    truncated = posture_timeline(hist.iloc[:cut], min_bars=60)

    assert not truncated.empty
    common = truncated.index
    pd.testing.assert_frame_equal(full.loc[common], truncated.loc[common])


def test_posture_timeline_labels_are_technical(uptrend_ohlcv):
    tl = posture_timeline(uptrend_ohlcv, min_bars=60)
    assert set(tl.columns) == {"tech_score", "label"}
    assert set(tl["label"]).issubset({"Bearish", "Neutral", "Bullish"})
