"""Signal backtest: point-in-time posture replay, forward-return event study,
and a portfolio equity curve.

Correctness rule: posture at date ``t`` is computed only from ``hist.iloc[:t+1]``.
The envelope band and regression/support overlays in :mod:`indicators` read the
whole window they are handed, so :func:`posture_timeline` re-runs
``add_indicators`` on each trailing slice rather than once on the full series.
This is O(N^2) per ticker — fine for a watchlist of dozens over a few years.
"""
from __future__ import annotations

import numpy as np
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
    # NOTE: strict inequality (<=) means exactly min_bars rows returns empty;
    # min_bars+1 rows produces one entry (the first bar after the warmup window).
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


def entry_events(timeline, entry_labels=("Bullish",)) -> list:
    """Dates where ``label`` transitions *into* ``entry_labels`` (de-overlapped).

    Collapsing runs of consecutive in-label bars to their first bar prevents
    autocorrelated daily samples from inflating the event count.
    """
    if timeline is None or timeline.empty or "label" not in timeline:
        return []
    is_in = timeline["label"].isin(entry_labels)
    prev = is_in.shift(1, fill_value=False)
    return list(timeline.index[is_in & ~prev])


def forward_returns(hist, entry_dates, horizons=("1m", "3m", "6m")) -> pd.DataFrame:
    """Forward returns from a next-day-open entry to each horizon's close."""
    horizons = list(horizons)
    if hist is None or hist.empty or not entry_dates:
        return pd.DataFrame(columns=horizons)

    opens = hist["Open"].to_numpy(float)
    closes = hist["Close"].to_numpy(float)
    pos = {ts: i for i, ts in enumerate(hist.index)}
    n = len(hist)

    rows: dict = {}
    for ts in entry_dates:
        i = pos.get(ts)
        if i is None or i + 1 >= n:
            continue
        entry = opens[i + 1]                          # execute at next-day open
        if not np.isfinite(entry) or entry <= 0:
            continue
        rec = {}
        for h in horizons:
            j = i + 1 + HORIZONS_BARS[h]
            rec[h] = (closes[j] / entry - 1) if j < n and np.isfinite(closes[j]) else np.nan
        rows[ts] = rec

    return pd.DataFrame.from_dict(rows, orient="index", columns=horizons)


def aggregate_event_stats(event_returns, baseline_returns=None) -> dict:
    """Per-horizon hit-rate / mean / median / win-loss, optionally baseline-relative."""
    stats: dict = {}
    for h in event_returns.columns:
        s = event_returns[h].dropna()
        wins, losses = s[s > 0], s[s < 0]
        d = {
            "n": int(s.size),
            "hit_rate": float((s > 0).mean()) if s.size else float("nan"),
            "mean": float(s.mean()) if s.size else float("nan"),
            "median": float(s.median()) if s.size else float("nan"),
            "avg_win": float(wins.mean()) if wins.size else float("nan"),
            "avg_loss": float(losses.mean()) if losses.size else float("nan"),
        }
        if baseline_returns is not None and h in baseline_returns:
            b = baseline_returns[h].dropna()
            d["baseline_mean"] = float(b.mean()) if b.size else float("nan")
            d["excess_mean"] = (d["mean"] - d["baseline_mean"]
                                if s.size and b.size else float("nan"))
        stats[h] = d
    return stats


def yearly_means(event_returns) -> dict:
    """Per-horizon mean forward return grouped by entry year (regime robustness)."""
    if event_returns is None or event_returns.empty:
        return {}
    by_year = event_returns.groupby(event_returns.index.year).mean()
    return {h: by_year[h].dropna().to_dict() for h in event_returns.columns}
