"""Classic chart patterns read off swing pivots — **display only**.

Double bottom / top, head & shoulders (and the inverse), ascending / descending
triangles and the bull flag, found in the zigzag of
:func:`stockanalysis.indicators.swing_pivots`. Every tolerance is in ATR units,
like the trade plan's, so a $50 name and a $1,700 name are judged alike. Only
*current* structure is reported: a pattern's last pivot must be among the two
most recent swings and within ``max_age`` bars of the latest bar.

Nothing here feeds :func:`stockanalysis.signals.decide_action`. Trading a
pattern breakout is an entry rule, and breakout-style entries (Donchian-20,
52-week highs) already measured no better than a random entry — see
``.claude/skills/tuning-signals/SKILL.md``. These overlays help a human read a
chart; they are not evidence that it will move. They are also common: with the
defaults, 44% of S&P 500 names showed at least one at their latest bar
(2026-09-25), and a stricter tolerance or depth barely moves that — two roughly
equal swings are simply ordinary.

Following the package-wide rule, degenerate input (no frame, no ATR14, too
little history) yields ``[]`` rather than raising.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import swing_pivots, value_at


def _zigzag(pivots: list[dict]) -> list[dict]:
    """Alternate highs and lows: of consecutive same-kind pivots keep the extreme."""
    out: list[dict] = []
    for p in pivots:
        if out and out[-1]["kind"] == p["kind"]:
            more_extreme = (p["price"] > out[-1]["price"] if p["kind"] == "high"
                            else p["price"] < out[-1]["price"])
            if more_extreme:
                out[-1] = p
        else:
            out.append(p)
    return out


def _recent(zz: list[dict], size: int, n_bars: int, max_age: int):
    """Windows of ``size`` consecutive swings ending at the last or second-to-last
    swing, most recent first, skipping any whose last swing is older than
    ``max_age`` bars."""
    for end in (len(zz), len(zz) - 1):
        if end - size >= 0 and n_bars - 1 - zz[end - 1]["i"] <= max_age:
            yield zz[end - size:end]


def _pattern(name: str, bias: str, pts: list[dict], level: float, confirmed: bool) -> dict:
    return {"name": name, "bias": bias,
            "points": [(p["date"], p["price"]) for p in pts],
            "level": float(level), "status": "confirmed" if confirmed else "forming"}


def _double(zz, d, ctx, *, bottom: bool):
    """Two equal lows (highs) either side of a real bounce; the neckline is the
    swing between them. Invalidated by a close beyond the pair."""
    first = "low" if bottom else "high"
    for a, mid, b in _recent(zz, 3, len(d), ctx["max_age"]):
        if a["kind"] != first or abs(a["price"] - b["price"]) > ctx["tol"]:
            continue
        pair = min(a["price"], b["price"]) if bottom else max(a["price"], b["price"])
        bounce = (mid["price"] - max(a["price"], b["price"]) if bottom
                  else min(a["price"], b["price"]) - mid["price"])
        if bounce < ctx["depth"]:
            continue
        after = d["Close"].iloc[b["i"] + 1:]
        broken = (after < pair - ctx["tol"]) if bottom else (after > pair + ctx["tol"])
        if broken.any():
            continue
        close = ctx["close"]
        return _pattern("Double Bottom" if bottom else "Double Top",
                        "bullish" if bottom else "bearish", [a, mid, b], mid["price"],
                        close > mid["price"] if bottom else close < mid["price"])
    return None


def _head_shoulders(zz, d, ctx, *, inverse: bool):
    """Three swings with the middle one furthest out and the outer two level; the
    neckline runs through the two swings between them, extended to today."""
    sign = -1.0 if inverse else 1.0            # read an inverse as a top, mirrored
    first = "low" if inverse else "high"
    for s1, n1, head, n2, s2 in _recent(zz, 5, len(d), ctx["max_age"]):
        if s1["kind"] != first:
            continue
        sh1, hd, sh2 = sign * s1["price"], sign * head["price"], sign * s2["price"]
        nk = max(sign * n1["price"], sign * n2["price"])
        if (hd - max(sh1, sh2) < ctx["tol"] or abs(sh1 - sh2) > 2 * ctx["tol"]
                or min(sh1, sh2) - nk < ctx["depth"]):
            continue
        slope = (n2["price"] - n1["price"]) / (n2["i"] - n1["i"])
        neck_now = n2["price"] + slope * (len(d) - 1 - n2["i"])
        close = ctx["close"]
        return _pattern("Inverse Head & Shoulders" if inverse else "Head & Shoulders",
                        "bullish" if inverse else "bearish", [s1, n1, head, n2, s2],
                        neck_now, close > neck_now if inverse else close < neck_now)
    return None


def _triangle(zz, d, ctx, *, ascending: bool):
    """Two level swings on one side, two converging on the other."""
    for window in _recent(zz, 4, len(d), ctx["max_age"]):
        h1, h2 = (p["price"] for p in window if p["kind"] == "high")
        l1, l2 = (p["price"] for p in window if p["kind"] == "low")
        if ascending:
            level, ok = max(h1, h2), abs(h1 - h2) <= ctx["tol"] and l2 - l1 >= ctx["tol"]
            ok = ok and level - l1 >= ctx["depth"]
        else:
            level, ok = min(l1, l2), abs(l1 - l2) <= ctx["tol"] and h1 - h2 >= ctx["tol"]
            ok = ok and h1 - level >= ctx["depth"]
        if ok:
            close = ctx["close"]
            return _pattern("Ascending Triangle" if ascending else "Descending Triangle",
                            "bullish" if ascending else "bearish", list(window), level,
                            close > level if ascending else close < level)
    return None


def _bull_flag(zz, d, ctx, *, pole_atr: float = 4.0, pole_bars: int = 15,
               max_flag_bars: int = 20, max_retrace: float = 0.5):
    """A fast rise (the pole) topping at the latest swing high, then a shallow
    pause: at most ``max_retrace`` of the pole given back."""
    highs = [p for p in zz if p["kind"] == "high"]
    if not highs:
        return None
    top = highs[-1]
    flag = d.iloc[top["i"] + 1:]
    if not 3 <= len(flag) <= max_flag_bars:
        return None
    pole_lows = d["Low"].iloc[max(0, top["i"] - pole_bars):top["i"] + 1]
    base_i = int(np.nanargmin(pole_lows.to_numpy(dtype=float)))
    base = {"date": pole_lows.index[base_i], "price": float(pole_lows.iloc[base_i])}
    pole = top["price"] - base["price"]
    if pole < pole_atr * ctx["atr"] or top["price"] - flag["Low"].min() > max_retrace * pole:
        return None
    return _pattern("Bull Flag", "bullish", [base, top], top["price"],
                    ctx["close"] > top["price"])


_DETECTORS = (
    lambda zz, d, c: _double(zz, d, c, bottom=True),
    lambda zz, d, c: _double(zz, d, c, bottom=False),
    lambda zz, d, c: _head_shoulders(zz, d, c, inverse=False),
    lambda zz, d, c: _head_shoulders(zz, d, c, inverse=True),
    lambda zz, d, c: _triangle(zz, d, c, ascending=True),
    lambda zz, d, c: _triangle(zz, d, c, ascending=False),
    _bull_flag,
)


def detect_patterns(df: pd.DataFrame, *, lookback: int = 120, pivot_window: int = 5,
                    tol_atr: float = 0.5, min_depth_atr: float = 2.0,
                    max_age: int = 40) -> list[dict]:
    """Current chart patterns in an indicator-enriched OHLCV frame.

    Returns ``[{name, bias, points: [(date, price), ...], level, status}]`` where
    ``level`` is the line a breakout crosses (neckline, flat side, flag top) and
    ``status`` is ``"confirmed"`` once the last close is beyond it, else
    ``"forming"``. Two swings count as "level" within ``tol_atr`` ATRs; a swing
    counts as a real move at ``min_depth_atr`` ATRs. ``pivot_window`` matches
    :func:`stockanalysis.indicators.find_support_resistance`'s default.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    atr, close = value_at(df, "ATR14"), value_at(df, "Close")
    if not (np.isfinite(atr) and atr > 0 and np.isfinite(close)):
        return []
    d = df.tail(lookback)
    zz = _zigzag(swing_pivots(d, pivot_window=pivot_window))
    ctx = {"atr": atr, "close": close, "tol": tol_atr * atr,
           "depth": min_depth_atr * atr, "max_age": max_age}
    return [p for p in (detect(zz, d, ctx) for detect in _DETECTORS) if p]
