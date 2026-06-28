"""Signal backtest: point-in-time posture replay, forward-return event study,
and a portfolio equity curve.

Correctness rule: posture at date ``t`` is computed only from ``hist.iloc[:t+1]``.
The envelope band and regression/support overlays in :mod:`indicators` read the
whole window they are handed, so :func:`posture_timeline` re-runs
``add_indicators`` on each trailing slice rather than once on the full series.
This is O(N^2) per ticker — fine for a watchlist of dozens over a few years.
"""
from __future__ import annotations

import pandas as pd

from .indicators import add_indicators
from .signals import TECHNICAL_COMPONENTS, compute_technical_posture

#: Forward-return horizons in trading days.
HORIZONS_BARS: dict[str, int] = {"1m": 21, "3m": 63, "6m": 126}


def posture_timeline(hist, *, mode="technical", fundamental_score=None,
                     components=None, min_bars: int = 60) -> pd.DataFrame:
    """Replay posture bar-by-bar, point-in-time.

    Returns a DataFrame indexed by date (from ``min_bars`` onward) with columns
    ``tech_score`` (0-len(components)) and ``label``. In ``technical`` mode
    ``label`` is the posture (Bearish/Neutral/Bullish); in ``composite`` mode it
    is the fused action (Buy/Hold/Watch) using ``fundamental_score``.
    """
    cols = ["tech_score", "label"]
    if hist is None or hist.empty or "Close" not in hist or len(hist) <= min_bars:
        return pd.DataFrame(columns=cols)

    comps = TECHNICAL_COMPONENTS if components is None else components
    n_comp = len(comps)
    f = 0.0 if fundamental_score is None else float(fundamental_score)

    out: dict = {}
    for i in range(min_bars, len(hist)):
        enriched = add_indicators(hist.iloc[: i + 1])           # trailing-only
        posture, tscore, _ = compute_technical_posture(enriched, components=comps)
        if mode == "composite":
            composite = 0.70 * (f / 6.0) + 0.30 * (tscore / n_comp)
            label = "Buy" if composite >= 0.60 else "Hold" if composite >= 0.40 else "Watch"
        else:
            label = posture
        out[hist.index[i]] = {"tech_score": tscore, "label": label}

    return pd.DataFrame.from_dict(out, orient="index", columns=cols)
