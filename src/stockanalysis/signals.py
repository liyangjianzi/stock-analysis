"""Signal engine: technical posture + the fused Buy/Hold/Watch matrix.

The technical score is **registry-driven**: each component in
:data:`TECHNICAL_COMPONENTS` is a pure predicate ``(df) -> bool`` worth +1. The
max score, composite divisor, posture cutoffs and ``detail`` keys all derive from
the registry, so adding/removing a component is a one-line edit there.

The default registry is a pullback/reversal pattern (uptrend, deep oversold dip,
shallow pullback, a confirming green bar, and a volume pickup) rather than a set
of independent bullish confirmations.

Score contract:
  - fundamental score: 0-6 (from :mod:`stockanalysis.screener`)
  - technical score:   0-len(TECHNICAL_COMPONENTS) (default 5)
  - composite = 0.70*(fund/6) + 0.30*(tech/len) -> Buy >=0.60, Hold >=0.40, else Watch
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
import pandas as pd

from .indicators import find_support_resistance


# --- Individual scoring components --------------------------------------------
# Each predicate is pure and NaN/short-data robust. They assume a non-empty df
# (compute_technical_posture guards emptiness before calling them).

def _at(df: pd.DataFrame, col: str, bars_ago: int = 0) -> float:
    """Value of ``col`` at ``bars_ago`` bars before the latest bar (0 = latest,
    i.e. ``df[col].iloc[-1-bars_ago]``); ``np.nan`` if the column is missing or
    there isn't enough history. The single place that implements the bracket
    notation (``[n]``) used in the predicate docstrings below."""
    if col not in df or len(df) <= bars_ago:
        return np.nan
    return df[col].iloc[-1 - bars_ago]


def _trend_up(df: pd.DataFrame) -> bool:
    """Uptrend intact and accelerating: Close > EMA50, and EMA50 has risen at
    least 2% over the last 20 bars: (EMA50 - EMA50[20]) / EMA50[20] >= 2%."""
    close, ema50 = _at(df, "Close"), _at(df, "EMA50")
    ema50_20ago = _at(df, "EMA50", 20)
    if not all(np.isfinite(v) for v in (close, ema50, ema50_20ago)) or ema50_20ago == 0:
        return False
    return bool(close > ema50 and (ema50 - ema50_20ago) / ema50_20ago >= 0.02)


def _dip_deep(df: pd.DataFrame) -> bool:
    """Yesterday's 3-day RSI showed a deep oversold dip: RSI(3)[1] < 25."""
    rsi3_prev = _at(df, "RSI3", 1)
    return bool(np.isfinite(rsi3_prev) and rsi3_prev < 25)


def _pullback_zone(df: pd.DataFrame) -> bool:
    """Price sits within one ATR of EMA50: (Close - EMA50) / ATR14 <= 1.0."""
    close, ema50, atr14 = _at(df, "Close"), _at(df, "EMA50"), _at(df, "ATR14")
    if not all(np.isfinite(v) for v in (close, ema50, atr14)) or atr14 == 0:
        return False
    return bool((close - ema50) / atr14 <= 1.0)


def _turn_confirm(df: pd.DataFrame) -> bool:
    """Today confirms a turn: Close > High[1] AND Close > Open."""
    close, high_prev, open_ = _at(df, "Close"), _at(df, "High", 1), _at(df, "Open")
    if not all(np.isfinite(v) for v in (close, high_prev, open_)):
        return False
    return bool(close > high_prev and close > open_)


def _vol_pattern(df: pd.DataFrame) -> bool:
    """A quiet spell followed by a pickup: SMA(Volume,5)[1] < VOL_SMA20 AND
    Volume >= 1.2 * VOL_SMA20."""
    vol_sma5_prev = _at(df, "VOL_SMA5", 1)
    vol_sma20, volume = _at(df, "VOL_SMA20"), _at(df, "Volume")
    if not all(np.isfinite(v) for v in (vol_sma5_prev, vol_sma20, volume)):
        return False
    return bool(vol_sma5_prev < vol_sma20 and volume >= 1.2 * vol_sma20)


TechnicalComponent = tuple[str, Callable[[pd.DataFrame], bool]]

#: Single source of truth for the technical score. Add/remove a (name, predicate)
#: tuple and the max score, composite divisor, posture cutoffs and detail keys all
#: follow automatically.
TECHNICAL_COMPONENTS: list[TechnicalComponent] = [
    ("trend_up", _trend_up),
    ("dip_deep", _dip_deep),
    ("pullback_zone", _pullback_zone),
    ("turn_confirm", _turn_confirm),
    ("vol_pattern", _vol_pattern),
]

#: Default posture cutoff: Bullish once at least 4 of the 5 default components fire
#: (score >= ceil(bull_frac * N); 2/3 -> ceil(3.33)=4 for N=5).
DEFAULT_BULL_FRAC = 2 / 3

#: Default Bearish cutoff, the mirror of :data:`DEFAULT_BULL_FRAC`: Bearish at no
#: more than 1 of the 5 default components (score <= floor(bear_frac * N);
#: 1/3 -> floor(1.67)=1 for N=5).
DEFAULT_BEAR_FRAC = 1 / 3


def _posture(score: int, max_score: int, bull_frac: float = DEFAULT_BULL_FRAC,
             bear_frac: float = DEFAULT_BEAR_FRAC) -> str:
    """Map a score to Bullish/Neutral/Bearish, scaling with the component count:
    Bearish at ``score <= floor(bear_frac * max_score)``, Bullish at
    ``score >= ceil(bull_frac * max_score)``, else Neutral.

    Both cutoffs derive from the registry size, so adding or removing a component
    rescales them together. Bearish is checked first, so on a degenerate registry
    where the bands would overlap the weaker label wins.
    """
    if max_score <= 0:
        return "Bearish"
    if score <= math.floor(bear_frac * max_score):
        return "Bearish"
    if score >= math.ceil(bull_frac * max_score):
        return "Bullish"
    return "Neutral"


def compute_technical_posture(df: pd.DataFrame,
                              components: list[TechnicalComponent] | None = None,
                              bull_frac: float = DEFAULT_BULL_FRAC,
                              bear_frac: float = DEFAULT_BEAR_FRAC):
    """Assess technical posture from an indicator-enriched df.

    Runs each predicate in ``components`` (default :data:`TECHNICAL_COMPONENTS`),
    awarding +1 per truthy result. Returns ``(posture_label, tech_score, detail)``
    where ``tech_score`` is 0-len(components) and ``detail`` maps each component
    name to its bool plus an unscored ``nearest_level`` support/resistance context.
    Posture scales with the component count (see :func:`_posture`). Robust to
    NaN/short data; a predicate that raises is treated as False.
    """
    components = TECHNICAL_COMPONENTS if components is None else components
    detail = {name: False for name, _ in components}
    detail["nearest_level"] = None
    if df is None or df.empty:
        return "Bearish", 0, detail

    for name, fn in components:
        try:
            detail[name] = bool(fn(df))
        except Exception:
            detail[name] = False

    # Context (not scored): nearest support/resistance level to the last close.
    close = df.iloc[-1].get("Close", np.nan)
    sr = find_support_resistance(df)
    if sr and np.isfinite(close):
        detail["nearest_level"] = min(sr, key=lambda L: abs(L["level"] - close))

    score = sum(detail[name] for name, _ in components)
    posture = _posture(score, len(components), bull_frac, bear_frac)
    return posture, score, detail


#: Shared bare-hex palette (no leading ``#``) for the ``Final Action Signal``/
#: ``Technical Posture`` labels this module produces. Single source of truth
#: for every renderer of those labels: ``outputs/excel.py`` wraps these in
#: ``openpyxl.PatternFill``, ``report.py`` prefixes ``#`` for inline CSS.
ACTION_COLORS = {"Buy": "B7E1CD", "Hold": "FCE8B2", "Watch": "D9D9D9"}
POSTURE_COLORS = {"Bullish": "B7E1CD", "Neutral": "FCE8B2", "Bearish": "F4C7C3"}


def generate_signals(screened: pd.DataFrame, tech_data: dict,
                     buy_thr: float = 0.60, hold_thr: float = 0.40) -> pd.DataFrame:
    """Fuse fundamentals (weight 0.70) and technicals (weight 0.30) into a
    final Buy / Hold / Watch action per stock.

    Returns a tidy DataFrame: Ticker, Sector, Fundamental Score,
    Technical Posture, Final Action Signal (+ supporting numeric columns).
    """
    if screened is None or screened.empty:
        return pd.DataFrame()

    rows = []
    for ticker, row in screened.iterrows():
        f_score = int(row.get("Fundamental_Score", 0))           # 0–6
        df_t = tech_data.get(ticker)
        posture, t_score, detail = compute_technical_posture(df_t)  # t_score 0-len(components)

        # Weighted composite: fundamentals dominate (0.70) over technicals (0.30).
        composite = 0.70 * (f_score / 6.0) + 0.30 * (t_score / len(TECHNICAL_COMPONENTS))

        if composite >= buy_thr:
            action = "Buy"
        elif composite >= hold_thr:
            action = "Hold"
        else:
            action = "Watch"

        rows.append({
            "Ticker": ticker,
            "Sector": row.get("Sector", "Unknown"),
            "Fundamental Score": f_score,
            "Technical Posture": posture,
            "Tech Score": t_score,
            "Composite": round(composite, 3),
            "Final Action Signal": action,
        })

    result = pd.DataFrame(rows)
    # Order: actionable first (Buy>Hold>Watch), then by composite strength.
    action_rank = {"Buy": 0, "Hold": 1, "Watch": 2}
    result["_rank"] = result["Final Action Signal"].map(action_rank)
    result = (result.sort_values(["_rank", "Composite"], ascending=[True, False])
                    .drop(columns="_rank").reset_index(drop=True))
    return result


def top_tickers(signal_matrix: pd.DataFrame, n: int | None = None) -> list[str]:
    """The strongest ``n`` tickers of a ranked signal matrix (all when ``n`` is None).

    :func:`generate_signals` returns its rows pre-ranked (Buy > Hold > Watch,
    then Composite descending), so the head of that frame *is* the top-pick
    list — this helper makes that contract explicit for callers who only want a
    subset (charts, profiles, a dashboard's "top 5"). Returns ``[]`` for an
    empty matrix, which carries no columns to index.
    """
    if signal_matrix is None or signal_matrix.empty:
        return []
    rows = signal_matrix if n is None else signal_matrix.head(n)
    return rows["Ticker"].tolist()
